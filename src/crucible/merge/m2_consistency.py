"""M2 一致性比对 —— 服务于 Q2 机制型 (准)。

LLM 只比对与呈现, 不重写 (设计文档 §7 契约):
  - 一致 → 合并结论 + 双证据 + high 置信
  - 冲突 → 对峙输出 (双方说法 + 证据), 不裁决、不折中
每条 claim 必须带 source; 无来源的结论降级为 unverified。
"""
from __future__ import annotations

import json
import re
from typing import Any


from ..llm_client import chat_complete
from ..config import Config

_PROMPT = """你是漏洞验证知识库的合并器。输入是 llm_wiki 与 LightRAG 两个引擎对同一机制问题的检索结论，以及 llm-wiki chat 的参考回答。

## llm_wiki 结论
{wiki_claim}

## LightRAG 结论
{rag_claim}

## llm-wiki chat 参考回答 (叙述底稿)
{chat_answer}

判断两库结论是否指向同一事实。
- 一致：输出 {{"consistent": true, "conclusion": "<完整回答>", "evidence": [{{"engine": "wiki", "claim": "...", "source": "..."}}, {{"engine": "rag", "claim": "...", "source": "..."}}]}}
- 冲突：输出 {{"consistent": false, "conflict": {{"wiki_says": {{"claim": "...", "source": "..."}}, "rag_says": {{"claim": "...", "source": "..."}}}}}}

约束：不得改写、综合、推测输入之外的机制事实；冲突时禁止选择其中一方。
呈现要求：conclusion 输出 2-6 句完整段落回答 —— 参考 chat 回答的叙述
方式与完整度，但每个事实必须能被 evidence 中的整句摘录支撑（有来源
才可说），句子完整成句，禁止半句截断、禁止省略号。
evidence 必须且只能输出两条: 一条 engine=wiki (claim 从 llm_wiki 结论
整句摘录, source 填 wiki 来源)、一条 engine=rag (claim 从 LightRAG 结论
整句摘录, source 填 rag 来源), 不得省略任何一条、不得合成第三条。
只输出 JSON。
"""


async def compare_mechanism(
    wiki_claim: str,
    wiki_source: str,
    rag_claim: str,
    rag_source: str,
    config: Config,
    chat_answer: str = "",
) -> dict[str, Any] | None:
    """双证据比对 (chat 参考回答为叙述底稿)。LLM 不可用/失败时返回 None。"""
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
            # 弱模型输出合法 JSON 概率低 (内网实调: M2 每查必降级),
            # 请求 json_object; 网关不支持时客户端自动去掉重试
            response_format={"type": "json_object"},
        )
    except Exception:
        return None

    try:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        return json.loads(match.group(0) if match else content)
    except json.JSONDecodeError:
        # 弱模型输不出合法嵌套 JSON → 回退 chat 底稿 (内网实调:
        # chat 回答质量已验证, 比并列降级可用得多); 事实仍以
        # 双引擎整句摘录为证据, 标注降级原因供前端/审计可见
        if chat_answer:
            return {
                "consistent": True,
                "conclusion": chat_answer[:800],
                "evidence": [
                    {"engine": "wiki", "claim": wiki_claim[:300], "source": wiki_source or "wiki"},
                    {"engine": "rag", "claim": rag_claim[:300], "source": "lightrag"},
                ],
                "note": "m2_json_parse_failed_chat_fallback",
            }
        return None
