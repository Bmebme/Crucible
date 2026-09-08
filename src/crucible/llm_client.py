"""统一 LLM 客户端 (裸 httpx): SSE 流式 + 单 JSON 双收 + 围栏剥除。

内网实调背景: 中转网关对 chat 请求返回 text/html, 直连端点强制
SSE (多个 data: 帧) —— OpenAI SDK 与"单 JSON 假设"都会炸
("str has no attribute choices" / "Expecting value: line 1 column 1")。
所有 LLM 调用统一走这里: rag 引擎 / 判别器 / 追问改写 / M2 一致性 /
L3 别名消解。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from .config import Config

logger = logging.getLogger("crucible.llm")

# 共享连接池: 每次调用新建 client 会重复 DNS+TCP+TLS 握手,
# 内网 DNS 慢时首包明显变慢 (内网实调: 第一次 llm call 慢)
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=300),
        )
    return _client


def _clean_fences(content: str) -> str:
    if content.lstrip().startswith("```"):
        content = re.sub(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$", "", content).strip()
    return content


async def chat_complete(
    config: Config,
    messages: list[dict[str, Any]],
    temperature: float = 0,
    response_format: dict | None = None,
    timeout: float = 600.0,
    max_tokens: int | None = None,
) -> str:
    """OpenAI 兼容 chat completions → 纯文本 content。

    SSE 多帧 delta.content 与单 JSON message.content 双收;
    response_format 网关 400 时自动去掉重试一次。
    """
    payload: dict[str, Any] = {
        "model": config.llm_model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    use_rf = bool(response_format)
    if use_rf:
        payload["response_format"] = response_format
    headers = {"Content-Type": "application/json"}
    if config.llm_api_key:
        headers["Authorization"] = f"Bearer {config.llm_api_key}"

    t0 = time.monotonic()
    logger.info("llm call 开始: model=%s", config.llm_model)
    url = f"{config.llm_base.rstrip('/')}/chat/completions"
    c = _get_client()
    r = await c.post(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code == 400 and use_rf:
        payload.pop("response_format", None)
        r = await c.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    ct = r.headers.get("content-type", "")
    text = r.text
    if "event-stream" in ct or text.lstrip().startswith("data:"):
        # SSE: 逐帧拼 delta.content
        parts: list[str] = []
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            d = line[5:].strip()
            if d == "[DONE]":
                break
            try:
                obj = json.loads(d)
            except json.JSONDecodeError:
                continue
            choices = obj.get("choices") or []
            if choices:
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    parts.append(delta["content"])
        content = "".join(parts)
    else:
        obj = json.loads(text)
        choices = obj.get("choices") or []
        if not choices:
            raise ValueError(f"网关响应无 choices: {text[:200]}")
        content = choices[0].get("message", {}).get("content", "") or ""
    content = _clean_fences(content)
    # 字节级清洗 (无效 UTF-8/控制字符, 网关输出可能混脏字节)
    content = content.encode("utf-8", errors="ignore").decode("utf-8")
    content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", content)
    logger.info(
        "llm call: model=%s (%.1fs, %d chars)",
        config.llm_model, time.monotonic() - t0, len(content),
    )
    if response_format:
        # 结构化输出 (实体抽取等) 格式漂移排障: 记开头供对照
        # (内网实调: 模型输出旧版 8 字段格式 → 1.5.7 解析 4 字段拒绝)
        logger.info("llm structured head: %s", content[:300])
    return content
