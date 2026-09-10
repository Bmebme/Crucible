"""统一 LLM 客户端: OpenAI 兼容 chat completions → 纯文本 content。

双实现 (CRUCIBLE_LLM_CLIENT=sdk 切换, 默认 httpx):
  - httpx 裸客户端: 内网实调主线。中转网关对 chat 返回 text/html、
    直连端点强制 SSE (多个 data: 帧) —— OpenAI SDK 与"单 JSON 假设"
    都会炸 ("str has no attribute choices" / "Expecting value")。
  - openai SDK: 标准兼容场景 (可选依赖 openai>=1.60), 流式收集,
    extra_body 传 thinking; openai 包未安装自动回退 httpx。
共用后处理: <think> 只留最后一个 </think> 之后 + 围栏剥除 + 字节清洗。
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
# openai SDK client 单例 (按 base_url+key 缓存)
_sdk: Any = None
_sdk_cache_key: tuple = ()


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=300),
        )
    return _client


def _should_disable_thinking(config: Config) -> bool:
    import os as _os

    return _os.environ.get("CRUCIBLE_LLM_NO_THINKING") == "on" or getattr(
        config, "llm_no_thinking", False
    )


def _use_sdk(config: Config) -> bool:
    import os as _os

    return _os.environ.get("CRUCIBLE_LLM_CLIENT") == "sdk" or getattr(
        config, "llm_client_mode", ""
    ) == "sdk"


def _clean_fences(content: str) -> str:
    if content.lstrip().startswith("```"):
        content = re.sub(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$", "", content).strip()
    return content


def _postprocess(content: str) -> str:
    """公共后处理: 围栏剥除 → <think> 规则 → 字节清洗。

    <think> 规则 (内网实调): 服务端思考拼接形态 <think>思考</think>答案,
    答案总在最后一段 </think> 之后 —— 出现 </think> 只留最后一个之后
    的内容 (思考段与段间过渡文字一并丢弃); 仅开头未闭合的 <think>
    视为纯思考清空。
    """
    content = _clean_fences(content)
    idx = content.rfind("</think>")
    if idx >= 0:
        content = content[idx + len("</think>"):].strip()
    elif content.startswith("<think>"):
        content = ""
    # 字节级清洗 (无效 UTF-8/控制字符, 网关输出可能混脏字节)
    content = content.encode("utf-8", errors="ignore").decode("utf-8")
    content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", content)
    return content


async def _chat_httpx(
    config: Config,
    messages: list[dict[str, Any]],
    temperature: float,
    response_format: dict | None,
    timeout: float,
    max_tokens: int | None,
) -> str:
    """裸 httpx 实现: SSE 多帧 delta.content 与单 JSON message.content
    双收。返回原始 content (后处理统一在 chat_complete)。"""
    payload: dict[str, Any] = {
        "model": config.llm_model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    # thinking 类模型思考模式默认开: 显式关掉 (网关透传; 部分网关
    # 拒绝未知字段 → 开关控制, 内网实调)
    if _should_disable_thinking(config):
        payload["thinking"] = {"type": "disabled"}
    use_rf = bool(response_format)
    if use_rf:
        payload["response_format"] = response_format
    headers = {"Content-Type": "application/json"}
    if config.llm_api_key:
        headers["Authorization"] = f"Bearer {config.llm_api_key}"

    url = f"{config.llm_base.rstrip('/')}/chat/completions"
    c = _get_client()
    r = await c.post(url, headers=headers, json=payload, timeout=timeout)
    if r.status_code == 400 and use_rf:
        payload.pop("response_format", None)
        r = await c.post(url, headers=headers, json=payload, timeout=timeout)
    # thinking 参数兼容: GLM-5.3 强制思考/部分网关不收该字段
    # (400 参数拒绝 / 404 路由失败 / 422 未知字段) → 去掉重试,
    # 思考走 reasoning_content 被忽略 + <think> 剥除兜底
    if r.status_code in (400, 404, 422) and "thinking" in payload:
        payload.pop("thinking", None)
        r = await c.post(url, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    ct = r.headers.get("content-type", "")
    text = r.text
    if "event-stream" in ct or text.lstrip().startswith("data:"):
        # SSE: 逐帧拼 delta.content
        parts: list[str] = []
        reasoning_chars = 0
        reasoning_deltas = 0
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
                rc = delta.get("reasoning_content")
                if rc:
                    reasoning_deltas += 1
                    reasoning_chars += len(rc)
                if delta.get("content"):
                    parts.append(delta["content"])
        # 探测: thinking 类模型的思考可能走 reasoning_content 通道,
        # 网关若把它并进 content 则思考混入正文 (内网实调)
        if reasoning_deltas:
            logger.info(
                "llm reasoning 流检测 (httpx): %d 帧, %d 字符 (已忽略, 仅 content 入正文)",
                reasoning_deltas, reasoning_chars,
            )
        return "".join(parts)
    obj = json.loads(text)
    choices = obj.get("choices") or []
    if not choices:
        raise ValueError(f"网关响应无 choices: {text[:200]}")
    return choices[0].get("message", {}).get("content", "") or ""


def _get_sdk_client(config: Config) -> Any:
    global _sdk, _sdk_cache_key
    from openai import AsyncOpenAI

    key = (config.llm_base, config.llm_api_key)
    if _sdk is None or _sdk_cache_key != key:
        _sdk = AsyncOpenAI(
            base_url=config.llm_base,
            api_key=config.llm_api_key or "EMPTY",
            timeout=httpx.Timeout(600.0, connect=30.0),
        )
        _sdk_cache_key = key
    return _sdk


async def _chat_sdk(
    config: Config,
    messages: list[dict[str, Any]],
    temperature: float,
    response_format: dict | None,
    timeout: float,
    max_tokens: int | None,
) -> str:
    """openai SDK 实现 (可选): stream=True 收集 delta.content,
    extra_body 传 thinking; 400/404/422 参数被拒逐个去掉重试
    (与 httpx 版同策略)。返回原始 content。"""
    from openai import APIStatusError

    client = _get_sdk_client(config)
    kwargs: dict[str, Any] = dict(
        model=config.llm_model,
        messages=messages,
        temperature=temperature,
        stream=True,
        timeout=timeout,
    )
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    extra: dict[str, Any] = {}
    if _should_disable_thinking(config):
        extra["thinking"] = {"type": "disabled"}
    if extra:
        kwargs["extra_body"] = extra
    use_rf = bool(response_format)
    if use_rf:
        kwargs["response_format"] = response_format

    async def _collect() -> str:
        stream = await client.chat.completions.create(**kwargs)
        parts: list[str] = []
        reasoning_deltas = 0
        reasoning_chars = 0
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            rc = getattr(delta, "reasoning_content", None)
            if not rc:
                rc = (getattr(delta, "model_extra", None) or {}).get("reasoning_content")
            if rc:
                reasoning_deltas += 1
                reasoning_chars += len(str(rc))
            if delta.content:
                parts.append(delta.content)
        if reasoning_deltas:
            logger.info(
                "llm reasoning 流检测 (sdk): %d 帧, %d 字符 (已忽略, 仅 content 入正文)",
                reasoning_deltas, reasoning_chars,
            )
        return "".join(parts)

    try:
        return await _collect()
    except APIStatusError as e:
        if e.status_code not in (400, 404, 422):
            raise
        if use_rf and "response_format" in kwargs:
            kwargs.pop("response_format", None)
            return await _collect()
        if kwargs.get("extra_body") and "thinking" in kwargs["extra_body"]:
            kwargs["extra_body"] = {
                k: v for k, v in kwargs["extra_body"].items() if k != "thinking"
            }
            return await _collect()
        raise


async def chat_complete(
    config: Config,
    messages: list[dict[str, Any]],
    temperature: float = 0,
    response_format: dict | None = None,
    timeout: float = 600.0,
    max_tokens: int | None = None,
) -> str:
    """OpenAI 兼容 chat completions → 纯文本 content (统一出口)。

    默认 httpx 裸客户端; CRUCIBLE_LLM_CLIENT=sdk (或 config.
    llm_client_mode) 时走 openai SDK, openai 包未安装自动回退 httpx。
    response_format 网关 400 时自动去掉重试 (两实现同策略)。
    """
    t0 = time.monotonic()
    client_name = "httpx"
    if _use_sdk(config):
        try:
            content = await _chat_sdk(
                config, messages, temperature, response_format, timeout, max_tokens
            )
            client_name = "sdk"
        except ImportError:
            logger.warning("CRUCIBLE_LLM_CLIENT=sdk 但 openai 包未安装, 回退 httpx 客户端")
            content = await _chat_httpx(
                config, messages, temperature, response_format, timeout, max_tokens
            )
    else:
        content = await _chat_httpx(
            config, messages, temperature, response_format, timeout, max_tokens
        )
    content = _postprocess(content)
    logger.info(
        "llm call: model=%s client=%s (%.1fs, %d chars)",
        config.llm_model, client_name, time.monotonic() - t0, len(content),
    )
    if response_format:
        # 结构化输出 (实体抽取等) 格式漂移排障: 记开头供对照
        # (内网实调: 模型输出旧版 8 字段格式 → 1.5.7 解析 4 字段拒绝)
        logger.info("llm structured head: %s", content[:300])
    return content
