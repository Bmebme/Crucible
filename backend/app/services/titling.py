"""验证记录标题生成 (LLM)。

背景: MCP 回写验证记录时, title 由 Agent 随手给 —— 常是一整句话
(长、带标点, 甚至含 "/"), 直接当文件名既难看又会拼出目录层级
(内网实调: ENOENT + 命名观感差)。这里用一个短 LLM 调用把「正文」
压成一个短标题, 只用于文件名与 frontmatter 的 title; 原标题保留。

约定 (与 services/formatting.summarize_enum 一致):
- 任何失败/超时/空结果都返回空串, 由调用方回落到清洗后的原标题;
  写盘路径绝不因为 LLM 不可用而失败。
- 开关: CRUCIBLE_TITLE_LLM=0 关闭 (默认开)。
"""
from __future__ import annotations

import logging
import os
import re

import httpx

logger = logging.getLogger("crucible.titling")

TITLE_TIMEOUT = 60.0        # 与 formatting.summarize_enum 一致; 超时即回落, 写盘不阻塞
MAX_TITLE_CHARS = 20        # 生成标题长度上限 (字符)
MAX_INPUT_CHARS = 1200      # 只喂正文开头, 省 token 也够判断主题
MAX_OUTPUT_CHARS = 80       # 模型话痨时的硬截断

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')

_PROMPT = """你是知识库的文件名生成器。根据下面的验证记录，生成一个简短标题：
1. 只输出标题本身——不要引号、不要解释、不要结尾标点
2. 中文，不超过 {max_chars} 个字
3. 不含 / \\ : * ? " < > | 等路径字符，不含换行
4. 保留关键标识（CVE 编号、组件名、问题类型等）

原记录标题（仅作参考，可以改写得更短）：{original}

记录内容：
{body}
"""


def enabled() -> bool:
    """写盘前的标题生成开关 (CRUCIBLE_TITLE_LLM=0/false/off/no 关闭)。"""
    return os.environ.get("CRUCIBLE_TITLE_LLM", "1").strip().lower() not in (
        "0", "false", "off", "no",
    )


def clean_title(raw: str) -> str:
    """模型输出 → 可安全用作文件名的单行短标题 (空串 = 不可用)。"""
    line = (raw or "").strip().splitlines()[0] if (raw or "").strip() else ""
    line = _UNSAFE.sub(" ", line)
    line = re.sub(r"\s+", " ", line).strip()
    line = line.strip("。.,;:：，、-—\"'“”‘’《》[]（）() ")
    if len(line) > MAX_OUTPUT_CHARS:
        line = line[:MAX_OUTPUT_CHARS].strip()
    return line


async def generate_title(
    original: str, body: str, llm_base: str, llm_key: str, llm_model: str
) -> str:
    """生成短标题; 未配置 key / 超时 / 报错 / 空结果 → 返回空串 (调用方回落)。"""
    if not llm_key:
        return ""
    payload = _PROMPT.format(
        max_chars=MAX_TITLE_CHARS,
        original=(original or "")[:200],
        body=(body or "")[:MAX_INPUT_CHARS],
    )
    try:
        async with httpx.AsyncClient(timeout=TITLE_TIMEOUT, trust_env=False) as c:
            resp = await c.post(
                f"{llm_base}/chat/completions",
                headers={"Authorization": f"Bearer {llm_key}"},
                json={
                    "model": llm_model,
                    "messages": [{"role": "user", "content": payload}],
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001 - 生成失败不影响写盘
        logger.warning("标题生成失败, 回落原标题: %s", exc)
        return ""
    return clean_title(raw)
