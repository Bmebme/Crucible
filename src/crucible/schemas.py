"""融合层输出契约 (对齐 DESIGN-search-strategy.md §5)。

所有输出带溯源; 冲突与差异并列呈现, 由 Agent/人裁决。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class QueryType(str, Enum):
    """三类查询 —— 倾向性由类型决定 (设计文档 §2.1)。"""

    ENUM = "Q1"       # 枚举型 · 全 · 核心指标: 召回率
    MECHANISM = "Q2"  # 机制型 · 准 · 核心指标: 精确率
    EXPERIENCE = "Q3" # 经验型 · 可信 · 核心指标: 采纳率


@dataclass
class IntentConfig:
    query_type: QueryType
    confidence: float = 1.0
    sub_queries: list[dict] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)  # 召回通道选择

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_type": self.query_type.value,
            "confidence": self.confidence,
            "sub_queries": self.sub_queries,
            "channels": self.channels,
        }


@dataclass
class WikiHit:
    """llm_wiki 页面引擎的一个召回命中。"""

    title: str
    path: str = ""
    score: float = 0.0
    snippet: str = ""
    verify_state: str | None = None   # M3 加权字段 (frontmatter)
    source: str = ""                 # 溯源

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "page",
            "title": self.title,
            "path": self.path,
            "score": self.score,
            "snippet": self.snippet,
            "verify_state": self.verify_state,
            "source": self.source,
        }


@dataclass
class RagEntity:
    """LightRAG 实体图引擎的一个实体。"""

    name: str
    entity_type: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "entity",
            "name": self.name,
            "type": self.entity_type,
            "description": self.description,
        }


@dataclass
class Difference:
    """M1 并集合并的差异项 —— 知识缺口信号 (设计文档 §5.1)。"""

    item: str
    only_in: str           # "wiki" | "rag"
    action: str = ""       # 回填建议

    def to_dict(self) -> dict[str, Any]:
        return {"item": self.item, "only_in": self.only_in, "action": self.action}


@dataclass
class FusionResponse:
    """统一融合响应 (设计文档 §3.1 输出层)。"""

    query: str
    routing: IntentConfig
    results: list[dict] = field(default_factory=list)   # 合并后的结果 (含 provenance)
    differences: list[Difference] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)  # M2 冲突对峙 (不裁决)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "routing": self.routing.to_dict(),
            "results": self.results,
            "differences": [d.to_dict() for d in self.differences],
            "conflicts": self.conflicts,
            "notes": self.notes,
        }
