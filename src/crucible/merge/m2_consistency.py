"""M2 一致性比对 —— 服务于 Q2 机制型 (准)。

LLM 只比对与呈现, 不重写 (设计文档 §7 契约):
  - 一致 → 合并结论 + 双证据 + high 置信
  - 冲突 → 对峙输出 (双方说法 + 证据), 不裁决、不折中
每条 claim 必须带 source; 无来源的结论降级为 unverified。
"""
from __future__ import annotations

from typing import Any


from ..llm_client import chat_complete
from ..config import Config

_PROMPT_SIMPLE = """你是漏洞验证知识库的合并器。下面是两个引擎对同一机制问题的检索结论和 chat 参考回答。

## llm_wiki 结论
{wiki_claim}

## LightRAG 结论
{rag_claim}

## llm-wiki chat 参考回答
{chat_answer}

请用自己的话写出一段连贯的整合回答, 直接回答该机制问题。要求: 只写与问题直接相关的事实, 不照抄输入段落; 每个论断在句末标注来源 (【wiki: 来源】或【rag】); 两库冲突时依据原文裁决并说明理由; 覆盖全部要点; 不编造输入之外的事实; 连续自然段。
"""

def _build_chat_style(
    query: str,
    wiki_claim: str,
    wiki_source: str,
    rag_claim: str,
    chat_answer: str,
    project_context: str,
) -> tuple[str, str]:
    """套用 py-llm-wiki chat 的生成形态 (内网实调: 同模型下 chat
    输出正常, 整合输出思维过程泛滥 —— 差异在形态不在模型):
    助手角色 + 项目背景 + 编号资料 + 极简问答指令, 无模板无禁令。
    """
    pages: list[str] = []
    if wiki_claim:
        pages.append(f"[1] {wiki_source or 'wiki'}\n{wiki_claim}")
    if rag_claim:
        pages.append(f"[2] lightrag 检索证据\n{rag_claim}")
    if chat_answer:
        pages.append(f"[3] 参考回答\n{chat_answer}")
    system = "你是这个知识库的助手，根据提供的知识库内容回答用户的问题，答案要完整、准确，并在相应位置标注来源编号（如 [1]）。"
    if project_context:
        system = (
            f"你是这个知识库的助手。项目背景:\n{project_context}\n\n"
            "根据提供的知识库内容回答用户的问题，答案要完整、准确，"
            "并在相应位置标注来源编号（如 [1]）。"
        )
    user = "## 知识库内容\n" + "\n\n".join(pages) + f"\n\n## 问题\n{query}"
    return system, user


_PROMPT_WEAK = _PROMPT_SIMPLE + """
严格按以下模板输出 (两个标记各出现一次):

【结论】在这里写整合回答正文。

【依据】在这里逐条列出支撑结论的事实要点, 每条带来源标注。

禁止事项 (违反任何一条输出都无效):
- 禁止输出思考过程、分析步骤或任务复述 (如"理解任务""分析输入"等字样)
- 禁止照抄或复述输入段落原文
- 禁止使用数字编号 (1. 2. 3.) 或列表格式
- 禁止在【结论】标记之前输出任何文字
- 禁止输出模板标记之外的任何内容
"""


async def compare_mechanism(
    wiki_claim: str,
    wiki_source: str,
    rag_claim: str,
    rag_source: str,
    config: Config,
    chat_answer: str = "",
    weak: bool = False,
    query: str = "",
    project_context: str = "",
) -> str | None:
    """双引擎整合回答 (自由文本, 无 JSON 契约)。

    weak=True (弱模型模式, 前端开关): 套用 py-llm-wiki chat 的
    生成形态 (助手角色 + 项目背景 + 编号资料 + 极简问答指令),
    输出经归一化 (剥编号/滤任务行)。
    weak=False: 简洁 prompt, 输出原样 (强模型路径保持干净)。
    LLM 不可用/失败返回 None —— 主形态 (分离证据) 不受影响。
    """
    if not config.llm_api_key:
        return None
    # 弱模型 + 超长上下文 = 预算花在思维步骤, 结论被截断 (内网实调):
    # 整合输入缩到 800×3 (全文在证据块里, 整合只需足够综合),
    # 显式 max_tokens 防止走网关默认短额度
    try:
        if weak:
            system, user = _build_chat_style(
                query,
                f"{wiki_claim[:800]}（来源: {wiki_source or 'unknown'}）",
                wiki_source or "wiki",
                f"{rag_claim[:800]}（来源: {rag_source or 'unknown'}）",
                chat_answer[:800],
                project_context[:1200],
            )
            content = await chat_complete(
                config,
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                temperature=0,
                max_tokens=2000,
            )
            return normalize_summary(content) or None
        prompt = _PROMPT_SIMPLE.format(
            wiki_claim=f"{wiki_claim[:800]}（来源: {wiki_source or 'unknown'}）",
            rag_claim=f"{rag_claim[:800]}（来源: {rag_source or 'unknown'}）",
            chat_answer=chat_answer[:800] or "（无）",
        )
        content = await chat_complete(
            config,
            [{"role": "system", "content": _PROMPT_SIMPLE},
             {"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=2000,
        )
    except Exception:
        return None
    return content.strip() or None


def _parse_template(content: str) -> str:
    """模板锚点解析: 取【结论】标记之后的正文, 标记之前的一切
    (思维过程/复述) 直接丢弃 —— 不依赖模型定位能力 (内网实调:
    弱模型输出被思维过程淹没)。无标记时返回原文。"""
    idx = content.find("【结论】")
    if idx >= 0:
        body = content[idx + len("【结论】"):]
        end = body.find("【依据】")
        if end >= 0:
            body = body[:end]
        return body.strip()
    return content


def normalize_summary(content: str) -> str:
    """弱模型输出归一化 (剥编号 + 滤思维过程任务描述行)。

    模型把 "1. 理解任务/分析输入/识别冲突" 这类思考步骤写进正文
    (内网实调)。所有进结论块的文本 (整合输出/chat 兜底) 统一过这里。
    """
    import re as _re

    content = (content or "").strip()
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    # ① 先剥编号 (弱模型爱输出编号列表, 含"引言+列表"混合形态;
    #    正常正文里的偶发 "1." 不误伤)
    matches = [_re.match(r"^\d+[.、)）]\s*(.*)", ln) for ln in lines]
    if sum(1 for m in matches if m) >= 2:
        lines = [(m.group(1) if m else ln) for ln, m in zip(lines, matches)]
    # ② 再滤思维过程的任务描述行 (剥号后 "理解任务/分析输入" 裸行)
    _TASK_LINE_RE = _re.compile(
        r"^(理解任务|分析输入|分析输入内容|识别冲突|整合结论|归纳总结|提炼结论|总结要点)\s*[:：]?\s*$"
    )
    lines = [ln for ln in lines if not _TASK_LINE_RE.match(ln)]
    # ③ 结论段定位 (弱模型会整段搬运原文, 不靠它定位 —— 确定性启发式:
    #    找最后一条以结论引导词开头的行, 从那里截到结尾; 无引导词保留全部)
    _CONCLUSION_MARKERS = ("结论", "综上", "总结", "因此", "最终", "整合后", "直接回答")
    marker_pos = -1
    for i, ln in enumerate(lines):
        if any(ln.startswith(m) for m in _CONCLUSION_MARKERS):
            marker_pos = i
    if marker_pos > 0:
        lines = lines[marker_pos:]
    return "\n".join(lines)


_CLEANUP_PROMPT = """下面是一段知识库整合输出的原文。这段输出通常前半部分是分析过程、任务复述或编号罗列, **真正的结论放在最后面**。

任务: 找到结论段的开头, 把从那里开始到结尾的内容**原样完整输出**, 包括其中的【wiki】【rag】等来源标记, 一字不差地保留。

注意: 原文开头若有"理解任务""分析输入"之类的思维过程描述, 那不是结论, 不要包含在输出里。

禁止: 不要改写、不要重新整理、不要摘要、不要补写任何内容、不要删改任何标记。

原文:
{content}
"""


async def extract_conclusion(content: str, config: Config) -> str | None:
    """二次问答: 从整合输出里提取干净结论 (弱模型专用开关)。

    弱模型擅长简单任务 —— 提取比整合容易, 输出质量稳定 (内网实调
    需求)。LLM 失败返回 None, 上层回退用原始整合输出。
    """
    if not config.llm_api_key or not content.strip():
        return None
    try:
        out = await chat_complete(
            config,
            [{"role": "user", "content": _CLEANUP_PROMPT.format(content=content[:1500])}],
            temperature=0,
            max_tokens=1500,
        )
    except Exception:
        return None
    out = out.strip()
    return out or None
