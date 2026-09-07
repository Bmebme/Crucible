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

_PROMPT = """你是漏洞验证知识库的合并器。下面是两个引擎对同一机制问题的检索结论和 chat 参考回答。

## llm_wiki 结论
{wiki_claim}

## LightRAG 结论
{rag_claim}

## llm-wiki chat 参考回答
{chat_answer}

直接输出整合后的完整回答。要求: 以原文为准, 每个论断在句末标注来源; 两库冲突时依据原文裁决并说明理由; 覆盖全部要点; 不编造输入之外的事实。输出必须是连续的自然段文字: 禁止数字编号 (1. 2. 3.)、禁止列表、禁止复述要求。
"""


async def compare_mechanism(
    wiki_claim: str,
    wiki_source: str,
    rag_claim: str,
    rag_source: str,
    config: Config,
    chat_answer: str = "",
) -> str | None:
    """双引擎对照小结 (自由文本, 无 JSON 契约)。

    弱模型友好 (内网实调: 严格嵌套 JSON 输不出 → 每查必降级)。
    LLM 不可用/失败返回 None —— 主形态 (分离证据) 不受影响。
    """
    if not config.llm_api_key:
        return None
    prompt = _PROMPT.format(
        wiki_claim=f"{wiki_claim}（来源: {wiki_source or 'unknown'}）",
        rag_claim=f"{rag_claim}（来源: {rag_source or 'unknown'}）",
        chat_answer=chat_answer[:2000] or "（无）",
    )
    try:
        content = await chat_complete(
            config,
            [{"role": "system", "content": _PROMPT},
             {"role": "user", "content": prompt}],
            temperature=0,
        )
    except Exception:
        return None
    content = content.strip()
    # 弱模型爱输出编号列表 (内网实调: 1/2/3 罗列, 含"引言+列表"
    # 混合形态) → 有 ≥2 行编号时剥去编号前缀, 正文行原样保留
    # (正常正文里的偶发 "1." 不会被误伤)
    import re as _re

    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    matches = [_re.match(r"^\d+[.、)）]\s*(.*)", ln) for ln in lines]
    if sum(1 for m in matches if m) >= 2:
        lines = [(m.group(1) if m else ln) for ln, m in zip(lines, matches)]
        content = "\n".join(lines)
    return content or None
