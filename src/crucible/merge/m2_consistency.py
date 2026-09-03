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

import httpx

from ..config import Config

_PROMPT = """你是漏洞验证知识库的合并器。输入是 llm_wiki 与 LightRAG 两个引擎对同一机制问题的检索结论。

## llm_wiki 结论
{wiki_claim}

## LightRAG 结论
{rag_claim}

判断两库结论是否指向同一事实。
- 一致：输出 {{"consistent": true, "conclusion": "<合并结论>", "evidence": [{{"engine": "wiki", "claim": "...", "source": "..."}}, {{"engine": "rag", "claim": "...", "source": "..."}}]}}
- 冲突：输出 {{"consistent": false, "conflict": {{"wiki_says": {{"claim": "...", "source": "..."}}, "rag_says": {{"claim": "...", "source": "..."}}}}}}

约束：不得改写、综合、推测输入之外的机制事实；冲突时禁止选择其中一方。
只输出 JSON。
"""


async def compare_mechanism(
    wiki_claim: str,
    wiki_source: str,
    rag_claim: str,
    rag_source: str,
    config: Config,
) -> dict[str, Any] | None:
    """双证据比对。LLM 不可用/失败时返回 None (上层降级为单源结论)。"""
    if not config.llm_api_key:
        return None
    prompt = _PROMPT.format(
        wiki_claim=f"{wiki_claim}（来源: {wiki_source or 'unknown'}）",
        rag_claim=f"{rag_claim}（来源: {rag_source or 'unknown'}）",
    )
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{config.llm_base}/chat/completions",
                headers={"Authorization": f"Bearer {config.llm_api_key}"},
                json={
                    "model": config.llm_model,
                    "messages": [
                        {"role": "system", "content": _PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError):
        return None

    try:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        return json.loads(match.group(0) if match else content)
    except json.JSONDecodeError:
        return None
