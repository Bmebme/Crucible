"""判别器 —— 查询类型分类 (设计文档 §2.2)。

两段式: 关键词规则先行 (覆盖高频句式约七成), LLM 兜底 (DeepSeek 等
OpenAI 兼容端点)。分类依据是「答案需要什么」, 不是问题主题。

输出 IntentConfig (query_type + confidence + sub_queries + channels)。
低置信度 (<0.7) 标 ambiguous: 不二次猜测, 由上层走保守策略。
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import Config
from .schemas import IntentConfig, QueryType

# 规则信号表 (设计文档 §2.2) —— 按疑问结构判别, 不看领域词汇。
# 顺序即优先级: 枚举信号最明确; 强经验信号 (以前/被拦/验证过) 优先于
# 机制信号 —— 「以前这类场景怎么打的」虽有"怎么", 主信号是"以前"。
# 弱经验信号 (裸「验证」) 排在机制之后 —— 「如何验证 X」仍走 Q2,
# 「上传验证」这类无机制信号的查询才落 Q3。
_RULES: list[tuple[re.Pattern, QueryType]] = [
    (re.compile(r"哪些|有哪些|类型|清单|列举|列出|全部|多少(个|种|类)?"), QueryType.ENUM),
    (re.compile(r"以前|历史|上次|最近|验证过|被拦|拦的|拦截|成功过|poc|payload", re.IGNORECASE), QueryType.EXPERIENCE),
    (re.compile(r"怎么|如何|调用|处理|版本|鉴权|实现|区别|是什么|工作原理|结果"), QueryType.MECHANISM),
    (re.compile(r"验证|验证记录|实测记录|误报"), QueryType.EXPERIENCE),
]

# 类型 → 召回通道 (设计文档 §5)
_CHANNELS = {
    QueryType.ENUM: ["wiki", "rag"],
    QueryType.MECHANISM: ["wiki", "rag"],
    QueryType.EXPERIENCE: ["wiki"],
}

# 类型 → 合并模式
_MERGE_MODE = {
    QueryType.ENUM: "M1",
    QueryType.MECHANISM: "M2",
    QueryType.EXPERIENCE: "M3",
}

_LLM_PROMPT = """将查询分为三类之一。分类依据是「答案需要什么」，不是问的主题。

Q1 枚举型（全）：答案需要穷尽列举一组事物
   → 问「有哪些 / 全部 / 清单 / 类型」，要的是完整性
Q2 机制型（准）：答案需要精确描述机制事实
   → 问「怎么 / 如何 / 调用 / 版本 / 鉴权实现」，要的是准确性
Q3 经验型（可信）：答案需要历史实证
   → 问「以前 / 历史 / 被拦 / 验证过 / 结果」，要的是可信度

判别要点：
1. 看疑问结构，不看领域词汇
2. 路径类问题不归 KB 查询——那是 Agent 的组合推理
3. 混合查询 → 拆子查询，标出主类型
4. 输出 confidence；低于 0.7 标 ambiguous

示例：
「这个产品有哪些文件处理组件？」→ Q1
「文件名是怎么进入 convert 命令的？」→ Q2
「上次 SSRF 验证是被什么拦的？」→ Q3

只输出 JSON，格式：
{"query_type": "Q1|Q2|Q3", "confidence": 0.9, "sub_queries": [{"type": "Q1", "text": "..."}]}

# 查询
"""


def classify_by_rules(query: str) -> IntentConfig | None:
    for pattern, qtype in _RULES:
        if pattern.search(query):
            return IntentConfig(
                query_type=qtype,
                confidence=1.0,
                channels=_CHANNELS[qtype],
            )
    return None


def matched_rule(query: str) -> str | None:
    """命中的规则正则 (分类可解释性: 前端展示「为什么判成这类」)。"""
    for pattern, _qtype in _RULES:
        if pattern.search(query):
            return pattern.pattern
    return None


async def classify_by_llm(query: str, config: Config) -> IntentConfig | None:
    """LLM 兜底分类。失败时返回 None (由上层走保守策略)。"""
    if not config.llm_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{config.llm_base}/chat/completions",
                headers={"Authorization": f"Bearer {config.llm_api_key}"},
                json={
                    "model": config.llm_model,
                    "messages": [
                        {"role": "system", "content": _LLM_PROMPT},
                        {"role": "user", "content": query},
                    ],
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError):
        return None

    try:
        # 容错: 提取第一个 JSON 对象
        match = re.search(r"\{.*\}", content, re.DOTALL)
        data = json.loads(match.group(0) if match else content)
        qtype = QueryType(data.get("query_type", "Q2"))
        confidence = float(data.get("confidence", 0.9))
        sub_queries = data.get("sub_queries") or []
    except (ValueError, json.JSONDecodeError):
        return None

    if confidence < 0.7:
        # 判不准就不判 (设计文档 §2.2 兜底策略)
        return None

    return IntentConfig(
        query_type=qtype,
        confidence=confidence,
        sub_queries=sub_queries,
        channels=_CHANNELS[qtype],
    )


async def classify(query: str, config: Config) -> IntentConfig:
    """两段式判别。规则未命中且 LLM 未给出高置信结果时走保守策略
    (全通道召回, 合并层按结果形态决定)。"""
    ruled = classify_by_rules(query)
    if ruled is not None:
        return ruled
    llm_result = await classify_by_llm(query, config)
    if llm_result is not None:
        return llm_result
    return IntentConfig(
        query_type=QueryType.MECHANISM,
        confidence=0.0,
        channels=["wiki", "rag"],
        sub_queries=[],
    )


def merge_mode(qtype: QueryType) -> str:
    return _MERGE_MODE[qtype]


# ---- 多轮追问改写 (设计文档 §10.2 采纳 WeKnora 的 QUERY_UNDERSTAND) ----

# 未消解指代 / 省略追问的锚点。只做检测, 消解交给 LLM (带历史)。
_FOLLOWUP_RES: list[re.Pattern] = [
    re.compile(r"那[个些]?"),
    re.compile(r"这[个些]?"),
    re.compile(r"它|它们"),
    re.compile(r"其"),
    re.compile(r"上述|前面|之前|刚才|刚刚|上面"),
]
_SHORT_ELLIPSIS_RE = re.compile(r"[呢吗]\s*[?？]*$")

_QUERY_LEN_MAX = 12  # 短问句 + 呢/吗 结尾视为省略追问

_REWRITE_PROMPT = """将多轮对话中的追问改写为自包含的独立查询。
规则:
1. 解析指代 (这个/那个/它/其/上述) 为历史提问中的具体对象
2. 补全省略成分 (如"接口呢" → 补出历史中的主体)
3. 保留原问题的类型结构 (枚举/机制/经验), 不改写成另一种问法
4. 只输出改写后的查询文本, 一行, 不要解释、不要引号

# 对话历史 (用户最近几轮提问, 最早在前)
{history}

# 当前追问
{query}
"""


def _is_followup(query: str) -> bool:
    """检测追问信号: 未消解指代, 或短问句以 呢/吗 结尾 (省略追问)。"""
    stripped = query.strip().rstrip("?？").strip()
    if any(r.search(query) for r in _FOLLOWUP_RES):
        return True
    return bool(_SHORT_ELLIPSIS_RE.search(stripped)) and len(stripped) <= _QUERY_LEN_MAX


async def _rewrite_by_llm(
    query: str, history: list[str], config: Config
) -> str | None:
    """LLM 指代消解 + 省略补全。失败返回 None (由上层原样降级)。"""
    if not config.llm_api_key:
        return None
    recent = "\n".join(f"- {h}" for h in history[-6:])
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{config.llm_base}/chat/completions",
                headers={"Authorization": f"Bearer {config.llm_api_key}"},
                json={
                    "model": config.llm_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": _REWRITE_PROMPT.format(
                                history=recent, query=query
                            ),
                        }
                    ],
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            rewritten = resp.json()["choices"][0]["message"]["content"].strip()
    except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError):
        return None
    if not rewritten or len(rewritten) > 200:
        return None
    return rewritten


async def rewrite_if_needed(
    query: str, history: list[str] | None, config: Config
) -> str:
    """多轮追问入口: 无历史或非追问时原样返回; 追问时 LLM 改写,
    改写失败原样降级 (检索按原查询走, 结果层按保守策略呈现)。"""
    if not history or not _is_followup(query):
        return query
    rewritten = await _rewrite_by_llm(query, history, config)
    return rewritten or query
