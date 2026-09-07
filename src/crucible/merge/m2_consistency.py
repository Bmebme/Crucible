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

_PROMPT = """你是漏洞验证知识库的合并器。输入是 llm_wiki 与 LightRAG 两个引擎对同一机制问题的检索结论，以及 llm-wiki chat 的参考回答。

## llm_wiki 结论
{wiki_claim}

## LightRAG 结论
{rag_claim}

## llm-wiki chat 参考回答
{chat_answer}

任务: 整合两个引擎的结论, 输出一个**完整连贯的机制回答** (普通文本, 不是 JSON、不是条目小结):

1. 以原文为准: 每个论断必须来自下方两库结论中的原文语句, 并在句末标注来源 (wiki: <来源> / rag / chat);
2. 分歧博弈: 两库说法冲突时, 逐条裁决 —— 依据所给原文说明采信哪一方及理由, 不得跳过冲突、不得和稀泥;
3. 完整性: 覆盖两库结论涉及的全部要点, 不省略任何机制细节;
4. 句子完整成句, 禁止半句截断、禁止省略号。

约束: 不得编造输入之外的事实。
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
    return content or None
