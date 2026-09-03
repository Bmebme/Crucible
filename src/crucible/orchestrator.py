"""融合编排器 —— 判别 → 双引擎召回 → 按类型合并 → 统一响应。

流程 (设计文档 §3.1):
  query → classify (倾向性配置) → 双引擎并行召回 → M1/M2/M3 之一 → 输出
"""
from __future__ import annotations

import asyncio
from typing import Any

from .classifier import classify, merge_mode, rewrite_if_needed
from .config import Config
from .engines.rag_engine import RagEngine
from .engines.wiki_engine import WikiEngine
from .merge import m2_consistency
from .merge.m1_union import union_merge
from .merge.m3_state import sort_by_verify_state
from .schemas import FusionResponse, QueryType, WikiHit


class FusionOrchestrator:
    def __init__(
        self,
        config: Config | None = None,
        project_id: str = "current",
        project_path: str = "",
    ):
        self.config = config or Config()
        self.project_id = project_id
        self.project_path = project_path
        self.wiki = WikiEngine(self.config.wiki_base)
        self.rag = RagEngine(self.config)

    async def run(
        self, query: str, *, env: str = "", history: list[str] | None = None
    ) -> FusionResponse:
        # 多轮追问: 先指代消解/省略补全 (§10.2), 所有引擎用消解后的查询。
        # history 为用户最近几轮提问 (最早在前)。
        resolved = await rewrite_if_needed(query, history, self.config)
        routing = await classify(resolved, self.config)
        mode = merge_mode(routing.query_type)
        response = FusionResponse(query=query, routing=routing)
        response.notes.append(f"merge_mode={mode}")
        if resolved != query:
            response.notes.append(f"rewritten_to={resolved}")

        if routing.query_type == QueryType.ENUM:
            await self._run_enum(resolved, response)
        elif routing.query_type == QueryType.EXPERIENCE:
            await self._run_experience(resolved, response, env)
        else:
            await self._run_mechanism(resolved, response)

        # 混合查询的子查询: 并行触发各自模式 (结果统一返回)
        for sub in routing.sub_queries:
            sub_type = QueryType(str(sub.get("type", "Q2")))
            sub_text = str(sub.get("text", ""))
            if not sub_text or sub_type == routing.query_type:
                continue
            sub_resp = await self.run(sub_text, env=env, history=history)
            response.results.extend(
                {"sub_query": sub_text, **r} for r in sub_resp.results
            )
            response.differences.extend(sub_resp.differences)
            response.conflicts.extend(sub_resp.conflicts)
        return response

    # ---- 三类执行路径 -------------------------------------------------

    async def _run_enum(self, query: str, resp: FusionResponse) -> None:
        """Q1: 双引擎枚举 → M1 并集合并。"""
        wiki_pages, rag_entities = await asyncio.gather(
            self.wiki.list_pages(self.project_id),
            self.rag.enumerate_entities(self.project_path, query),
        )
        merged = union_merge(wiki_pages, [e.name for e in rag_entities])
        resp.results = [
            {"kind": "item", "name": name, "provenance": ["union"]}
            for name in merged.union
        ]
        resp.differences = merged.differences
        resp.notes.append(
            f"union={len(merged.union)} wiki={len(merged.wiki_items)} "
            f"rag={len(merged.rag_items)} differences={len(merged.differences)}"
        )

    async def _run_experience(self, query: str, resp: FusionResponse, env: str) -> None:
        """Q3: wiki verify_state 加权 → M3 状态排序 (原样引用)。"""
        hits: list[WikiHit] = await self.wiki.search(self.project_id, query, limit=20)
        items: list[dict[str, Any]] = []
        for hit in hits:
            if hit.path and "verification" not in hit.path:
                continue
            fm = (
                await self.wiki.read_page_frontmatter(self.project_id, hit.path)
                if hit.path
                else {}
            )
            items.append({**hit.to_dict(), "verify_state": fm.get("verify_state")})
        ordered = sort_by_verify_state(items, env=env, query=query)
        resp.results = [
            {
                **h.item,
                "weight": h.weight,
                "state": h.state,
                "note": h.note,
                "provenance": ["wiki", "M3"],
            }
            for h in ordered
            if h.weight > 0
        ]
        resp.notes.append(f"verified_weighted={len(resp.results)}")

    async def _run_mechanism(self, query: str, resp: FusionResponse) -> None:
        """Q2: 双引擎召回 → M2 一致性比对 (LLM 只比对不重写)。"""
        wiki_hits, rag_answer = await asyncio.gather(
            self.wiki.search(self.project_id, query, limit=3),
            self.rag.query(self.project_path, query, mode="hybrid"),
        )
        wiki_top = wiki_hits[0] if wiki_hits else None
        if wiki_top is None and not rag_answer:
            resp.notes.append("两引擎均无召回")
            return

        compared = await m2_consistency.compare_mechanism(
            wiki_claim=(wiki_top.title + (f": {wiki_top.snippet}" if wiki_top else "")),
            wiki_source=wiki_top.path if wiki_top else "",
            rag_claim=rag_answer[:600],
            rag_source="lightrag",
            config=self.config,
        )
        if compared is None:
            # LLM 不可用: 降级为单源并列 (设计文档 §9.3)
            if wiki_top:
                resp.results.append({**wiki_top.to_dict(), "provenance": ["wiki"], "confidence": "degraded"})
            if rag_answer:
                resp.results.append({"kind": "entity", "name": "LightRAG 结论", "snippet": rag_answer[:300], "provenance": ["rag"], "confidence": "degraded"})
            resp.notes.append("M2 降级: LLM 不可用, 单源并列输出")
            return

        if compared.get("consistent"):
            resp.results.append(
                {
                    "kind": "conclusion",
                    "conclusion": compared.get("conclusion"),
                    "evidence": compared.get("evidence"),
                    "confidence": "high",
                    "provenance": ["wiki", "rag", "M2"],
                }
            )
        else:
            resp.conflicts.append(compared.get("conflict") or {})
            if wiki_top:
                resp.results.append({**wiki_top.to_dict(), "provenance": ["wiki"]})
            if rag_answer:
                resp.results.append({"kind": "entity", "name": "LightRAG 结论", "snippet": rag_answer[:300], "provenance": ["rag"]})
            resp.notes.append("冲突对峙输出: 不裁决, 由 Agent/人裁决后回写")
