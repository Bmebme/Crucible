"""融合编排器 —— 判别 → 双引擎召回 → 按类型合并 → 统一响应。

流程 (设计文档 §3.1):
  query → classify (倾向性配置) → 双引擎并行召回 → M1/M2/M3 之一 → 输出
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .classifier import classify, merge_mode, rewrite_if_needed
from .config import Config
from .engines.rag_engine import RagEngine
from .engines.wiki_engine import WikiEngine
from .merge import m2_consistency
from .merge.aliases import candidate_pairs, load_alias_dict, resolve_llm_pairs
from .merge.m1_union import normalize_name, union_merge
from .merge.m3_state import sort_by_verify_state
from .schemas import Citation, FusionResponse, QueryType, WikiHit


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

    async def run_enum(self, hint: str) -> FusionResponse:
        """Q1 枚举入口 (CLI/MCP 共用): 判别 → M1 并集合并 (含 L2/L3)。"""
        routing = await classify(f"有哪些{hint}", self.config)
        resp = FusionResponse(query=f"enum:{hint}", routing=routing)
        resp.notes.append(f"merge_mode={merge_mode(routing.query_type)}")
        await self._run_enum(hint, resp)
        return resp

    async def _run_enum(self, query: str, resp: FusionResponse) -> None:
        """Q1: 双引擎枚举 → M1 并集合并 (含 L2/L3 名字对齐, §5.5)。

        wiki 通道按 hint 检索相关页 (Q1 的「全」= 全部相关, 不是全库清单;
        list_pages 全量只作检索失败时的降级)。
        """
        wiki_hits, rag_entities = await asyncio.gather(
            self.wiki.search(self.project_id, f"{query} 有哪些", limit=100),
            self.rag.enumerate_entities(self.project_path, query),
        )
        wiki_pages = []
        for h in wiki_hits:
            if not h.path:
                continue
            rel = h.path[:-3] if h.path.endswith(".md") else h.path
            if rel in ("wiki/index", "wiki/log", "wiki/overview"):
                continue  # 导航页不是知识实体
            wiki_pages.append(h.path)
        if not wiki_pages:
            # 降级: 检索无结果时退全量清单 (保住召回)
            wiki_pages = await self.wiki.list_pages(self.project_id)
        rag_names = [e.name for e in rag_entities]
        mode = self.config.alias_mode

        # L2 词典: 仅 l2+l3 模式加载 (l3 跳过词典, off 全关)
        alias_dict = None
        if mode == "l2+l3" and self.project_path:
            alias_dict = load_alias_dict(
                Path(self.project_path) / self.config.aliases_file
            )

        # L3 LLM 消解: 第一遍合并后, 对剩余差异做候选剪枝 + 批量判定
        llm_same: set[tuple[str, str]] = set()
        if mode in ("l2+l3", "l3"):
            first = union_merge(wiki_pages, rag_names, aliases=alias_dict)
            wiki_left = {
                normalize_name(d.item)
                for d in first.differences
                if d.only_in == "wiki"
            }
            rag_left = {
                normalize_name(d.item)
                for d in first.differences
                if d.only_in == "rag"
            }
            pairs = candidate_pairs(wiki_left, rag_left)
            llm_same = await resolve_llm_pairs(pairs, self.config)
            if pairs:
                resp.notes.append(
                    f"l3_candidates={len(pairs)} l3_matched={len(llm_same)}"
                )

        merged = union_merge(
            wiki_pages, rag_names, aliases=alias_dict, llm_same=llm_same or None
        )
        # 枚举条目携带简介: wiki 侧带检索 snippet, rag 侧带实体 description
        # (Agent 的「目录」要有书名+一句话简介, 不能只有裸名字)
        wiki_meta = {h.path: (h.title, h.snippet) for h in wiki_hits if h.path}
        rag_meta = {e.name: (e.entity_type, e.description) for e in rag_entities}
        resp.results = []
        for name in merged.union:
            item: dict[str, Any] = {"kind": "item", "name": name, "provenance": ["union"]}
            if name in wiki_meta:
                item["snippet"] = wiki_meta[name][1]
            elif name in rag_meta:
                item["entity_type"] = rag_meta[name][0]
                item["description"] = rag_meta[name][1]
            resp.results.append(item)
        resp.differences = merged.differences
        resp.notes.append(
            f"union={len(merged.union)} wiki={len(merged.wiki_items)} "
            f"rag={len(merged.rag_items)} differences={len(merged.differences)} "
            f"alias_mode={mode}"
        )
        for note in merged.alias_notes:
            resp.notes.append(f"alias: {note}")

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
            items.append(
                {
                    **hit.to_dict(),
                    "verify_state": fm.get("verify_state"),
                    "verify_env": fm.get("verify_env"),
                }
            )
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
        """Q2: 双引擎召回 → M2 一致性比对 (LLM 只比对不重写) + 引用层接地。"""
        wiki_hits, rag_answer, rag_context = await asyncio.gather(
            self.wiki.search(self.project_id, query, limit=3),
            self.rag.query(self.project_path, query, mode="hybrid"),
            self.rag.query_context(self.project_path, query, mode="hybrid"),
        )
        # 引用层: wiki 命中自带引用; rag 侧从检索上下文 chunk 摘原文
        rag_citations = [
            Citation(
                source="rag",
                chunk_id=c.reference_id,
                heading_path=c.headings,
                excerpt=c.content[:400],
            )
            for c in rag_context[:3]
        ]
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
                resp.results.append({
                    "kind": "entity", "name": "LightRAG 结论", "snippet": rag_answer[:300],
                    "provenance": ["rag"], "confidence": "degraded",
                    "citations": [c.to_dict() for c in rag_citations],
                })
            resp.notes.append("M2 降级: LLM 不可用, 单源并列输出")
            return

        all_citations = (wiki_top.citations if wiki_top else []) + rag_citations
        if compared.get("consistent"):
            # 强制接地: 无引用不输出合并结论 (宁缺毋滥, 守 faithfulness)
            if not all_citations:
                resp.notes.append("M2 合并结论无引用支撑, 降级为并列输出")
                if wiki_top:
                    resp.results.append({**wiki_top.to_dict(), "provenance": ["wiki"], "confidence": "degraded"})
                if rag_answer:
                    resp.results.append({
                        "kind": "entity", "name": "LightRAG 结论", "snippet": rag_answer[:300],
                        "provenance": ["rag"], "confidence": "degraded",
                        "citations": [c.to_dict() for c in rag_citations],
                    })
                return
            resp.results.append(
                {
                    "kind": "conclusion",
                    "conclusion": compared.get("conclusion"),
                    "evidence": compared.get("evidence"),
                    "confidence": "high",
                    "provenance": ["wiki", "rag", "M2"],
                    "citations": [c.to_dict() for c in all_citations],
                }
            )
        else:
            resp.conflicts.append(compared.get("conflict") or {})
            if wiki_top:
                resp.results.append({**wiki_top.to_dict(), "provenance": ["wiki"]})
            if rag_answer:
                resp.results.append({
                    "kind": "entity", "name": "LightRAG 结论", "snippet": rag_answer[:300],
                    "provenance": ["rag"],
                    "citations": [c.to_dict() for c in rag_citations],
                })
            resp.notes.append("冲突对峙输出: 不裁决, 由 Agent/人裁决后回写")
