"""融合编排器 —— 判别 → 双引擎召回 → 按类型合并 → 统一响应。

流程 (设计文档 §3.1):
  query → classify (倾向性配置) → 双引擎并行召回 → M1/M2/M3 之一 → 输出
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("crucible.orchestrator")

from .classifier import classify, merge_mode, rewrite_if_needed
from .config import Config
from .engines.rag_engine import RagEngine
from .engines.wiki_engine import WikiEngine
from .merge import m2_consistency
from .merge.aliases import candidate_pairs, load_alias_dict, resolve_llm_pairs
from .merge.m1_union import normalize_name, union_merge
from .merge.m3_state import sort_by_verify_state
from .engines.wiki_engine import find_heading
from .schemas import Citation, FusionResponse, IntentConfig, QueryType, WikiHit


def _sentence_slice(text: str, max_chars: int = 800) -> str:
    """按句子边界截断: 超过 max_chars 时回退到最后一个句末标点/换行,
    引用与简介不再半截话 (内网实调反馈)。"""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    best = -1
    for sep in ("。", "！", "？", "\n", ". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > max_chars * 0.5 and idx > best:
            best = idx + (1 if sep not in (". ", "! ", "? ") else 2)
    return cut[:best] if best > 0 else cut


_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
# ASCII 树形/图例行 (│ ├ └ | mermaid 语法): 展示层噪音, 过滤
_TREE_LINE_RE = re.compile(r"^[\s│├└┌┐┘└─|]+.*$", re.MULTILINE)


async def _save_query_history(project_path: str, query: str, response: FusionResponse) -> None:
    """每次查询的完整响应落盘 (内网无法复制外网, 供人肉转述/分析;
    也为历史查询页打底)。失败不影响查询。"""
    import json as _json

    try:
        hist_dir = Path(project_path) / "query-history"
        hist_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        qid = abs(hash(query)) % 100000
        fd, tmp = __import__("tempfile").mkstemp(dir=str(hist_dir), suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                _json.dump(
                    {"query": query, "ts": ts, **response.to_dict()},
                    f, ensure_ascii=False, indent=2,
                )
            os.replace(tmp, hist_dir / f"{ts}-{qid}.json")
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    except Exception as e:
        logger.warning("查询历史落盘失败: %s", e)


def _sanitize(text: str) -> str:
    """字节级清洗: 剔除无效 UTF-8 与除 \\t\\n\\r 外的控制字符。

    内网实调: 大段文本字段 (rag 引用 excerpt) 出现坏 JSON, 疑为
    数据脏字节 (LightRAG chunk/网关输出混入异常序列)。所有进响应的
    大文本统一过这里。
    """
    if not text:
        return ""
    text = text.encode("utf-8", errors="ignore").decode("utf-8")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def _clean_rag_display(text: str, max_chars: int = 600) -> str:
    """RAG 展示文本清洗: LightRAG 混合输出 = LLM 文字 + 原文 chunk
    (含 ASCII 树/mermaid 代码块), 直接截断呈现残缺乱码 (内网实调)。
    剥代码块、滤树形行与 markdown 结构行, 只留成段正文再句界截断。"""
    text = _sanitize(text)
    text = _CODE_BLOCK_RE.sub("", text)
    text = "\n".join(
        ln
        for ln in text.splitlines()
        if not _TREE_LINE_RE.match(ln)
        and not ln.strip().startswith(("- [", "---", "## ", "```"))
        and ln.strip()
    )
    return _sentence_slice(text, max_chars)


def _strip_frontmatter(content: str) -> str:
    """引用 excerpt 去掉 YAML frontmatter (verify_state 等元数据不该进原文引用)。"""
    content = content or ""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:].strip()
    return content


def _snippet_around(content: str, snippet: str, max_chars: int = 200) -> str:
    """把搜索片段扩展成完整句子窗口: 定位 snippet 在原文中的位置,
    从所在句开头取到 max_chars 并收在句末; 找不到则退全文首句。"""
    content = content or ""
    snippet = (snippet or "").strip()
    if not content:
        return snippet
    norm_c = re.sub(r"\s+", " ", content)
    if snippet:
        pos = norm_c.find(re.sub(r"\s+", " ", snippet)[:60])
        if pos >= 0:
            start = max(
                norm_c.rfind("。", 0, pos) + 1,
                norm_c.rfind("\n", 0, pos) + 1,
                0,
            )
            return _sentence_slice(norm_c[start:pos + max_chars], max_chars)
    return _sentence_slice(norm_c, max_chars)


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
        self, query: str, *, env: str = "", history: list[str] | None = None,
        cleanup: bool = False, budget: float | None = None,
    ) -> FusionResponse:
        """budget: 整体等待上限 (秒, 与前端「等待上限」开关同源)。
        到点立即返回已完成部分: LLM 环节取消降级, llm-wiki/RAG 原文证据照常
        (内网实调: 查询被 LLM 拖到前端超时, 结果全丢)。"""
        # 多轮追问: 先指代消解/省略补全 (§10.2), 所有引擎用消解后的查询。
        # history 为用户最近几轮提问 (最早在前)。
        t0 = time.monotonic()
        resolved = await rewrite_if_needed(query, history, self.config)
        # 判别预算: 整体上限的 1/4, 封顶 30s; 超时走保守策略 (Q2 全通道)
        classify_budget = min(budget * 0.25, 30.0) if budget and budget > 0 else None
        classify_timed_out = False
        try:
            if classify_budget:
                routing = await asyncio.wait_for(
                    classify(resolved, self.config), timeout=classify_budget
                )
            else:
                routing = await classify(resolved, self.config)
        except asyncio.TimeoutError:
            routing = IntentConfig(
                query_type=QueryType.MECHANISM, confidence=0.0,
                channels=["wiki", "rag"], sub_queries=[],
            )
            classify_timed_out = True
        response = FusionResponse(query=query, routing=routing)
        response.timings["判别"] = time.monotonic() - t0
        if classify_timed_out:
            response.notes.append("判别超时, 走保守策略 (Q2 全通道)")
        mode = merge_mode(routing.query_type)
        response.notes.append(f"merge_mode={mode}")
        if resolved != query:
            response.notes.append(f"rewritten_to={resolved}")

        if routing.query_type == QueryType.ENUM:
            await self._run_enum(resolved, response)
        elif routing.query_type == QueryType.EXPERIENCE:
            await self._run_experience(resolved, response, env)
        else:
            # 机制阶段预算 = 整体上限扣掉已消耗 (判别/改写)
            remaining = None
            if budget and budget > 0:
                remaining = max(budget - (time.monotonic() - t0), 1.0)
            await self._run_mechanism(resolved, response, cleanup, budget=remaining)
        response.timings["总耗时"] = time.monotonic() - t0
        await _save_query_history(self.project_path, query, response)

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
        async def timed(key: str, coro):
            t1 = time.monotonic()
            r = await coro
            resp.timings[key] = resp.timings.get(key, 0.0) + (time.monotonic() - t1)
            return r

        wiki_hits, rag_entities = await asyncio.gather(
            timed("wiki召回", self.wiki.search(self.project_id, f"{query} 有哪些", limit=100)),
            timed("rag召回", self.rag.enumerate_entities(self.project_path, query)),
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
        t1 = time.monotonic()
        hits: list[WikiHit] = await self.wiki.search(self.project_id, query, limit=20)
        resp.timings["wiki召回"] = time.monotonic() - t1
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
        resp.timings["合并"] = 0.0  # M3 纯排序无 LLM
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

    async def _run_mechanism(self, query: str, resp: FusionResponse, cleanup: bool = False,
                             budget: float | None = None) -> None:
        """Q2: 双引擎召回 → M2 一致性比对 (LLM 只比对不重写) + 引用层接地。

        budget 到点后取消未完成环节 (chat LLM 最可能被取消), 用已完成
        结果组装返回 —— 等待上限不再转圈到死, llm-wiki 原文证据照常。"""
        t_mech0 = time.monotonic()

        def mk(name: str, coro):
            async def _wrap():
                t1 = time.monotonic()
                try:
                    return await coro
                finally:
                    resp.timings[name] = time.monotonic() - t1
            return asyncio.create_task(_wrap())

        async def safe_chat() -> tuple[str, list[dict]]:
            try:
                return await self.wiki.chat_answer(self.project_id, query)
            except asyncio.CancelledError:
                raise  # 等待上限取消: 由上层降级
            except Exception as e:
                logger.warning("wiki chat 参考回答获取失败: %s", e)
                return "", []

        tasks = {
            "wiki召回": mk("wiki召回", self.wiki.search(self.project_id, query, limit=3)),
            "rag召回": mk("rag召回", self.rag.query(self.project_path, query, mode="hybrid")),
            "rag上下文": mk("rag上下文", self.rag.query_context(self.project_path, query, mode="hybrid")),
            "chat参考": mk("chat参考", safe_chat()),
        }
        if budget and budget > 0:
            done, pending = await asyncio.wait(tasks.values(), timeout=budget)
        else:
            done, pending = await asyncio.wait(tasks.values())
        if pending:
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)  # 回收取消异常
            resp.timed_out = True
            resp.notes.append(
                "等待上限触发, 已取消: "
                + ", ".join(n for n, t in tasks.items() if t in pending)
            )

        def take(name: str, default):
            t = tasks[name]
            if t in done and not t.cancelled():
                try:
                    return t.result()
                except Exception as e:
                    logger.warning("%s 失败: %s", name, e)
            return default

        wiki_hits = take("wiki召回", [])
        rag_answer = take("rag召回", "")
        rag_context = take("rag上下文", [])
        chat_answer, chat_refs = take("chat参考", ("", []))
        # 用户契约: chat 内部检索到的页面更详实 → 其引用页作为 wiki
        # 证据来源, 本服务搜索降为兜底 (内网实调: 自有搜索被霸榜页
        # 挤占, chat 引用页才是真正详实的)
        if chat_refs:
            wiki_hits = [
                WikiHit(
                    title=r.get("title") or r.get("path", ""),
                    path=r.get("path", ""),
                    snippet=(r.get("snippet") or "")[:500],
                    citations=[Citation(source="wiki", path=r.get("path", ""),
                                        excerpt=(r.get("snippet") or "")[:500])]
                    if r.get("path") else [],
                )
                for r in chat_refs
            ]
            resp.notes.append(f"wiki侧使用 chat 引用页 ({len(wiki_hits)} 页)")
        # 引用层: wiki 命中自带引用; rag 侧从检索上下文 chunk 摘原文
        rag_citations = [
            Citation(
                source="rag",
                chunk_id=c.reference_id,
                heading_path=c.headings,
                excerpt=_clean_rag_display(c.content, 800),
            )
            for c in rag_context[:3]
        ]
        wiki_top = wiki_hits[0] if wiki_hits else None
        # 全量呈现: 所有 wiki 命中都读原文 (之前只读第一名, 其余丢弃
        # —— 内网实调: page read 每次都只出现一次)
        wiki_raw: dict[str, str] = {}
        if wiki_hits:
            async def read_hit(h: WikiHit) -> tuple[str, str]:
                try:
                    return h.path, await self.wiki.read_page_content(
                        self.project_id, h.path
                    )
                except Exception:
                    return h.path, ""

            pairs = await asyncio.gather(*(read_hit(h) for h in wiki_hits[:3]))
            wiki_raw = dict(pairs)
        wiki_contents = {
            p: _sentence_slice(_strip_frontmatter(c), 2000) for p, c in wiki_raw.items()
        }
        wiki_content = wiki_contents.get(wiki_top.path, "") if wiki_top else ""
        # 引用段落级定位: 给 wiki 顶部引用补 heading_path +
        # 整句 excerpt + 完整句子的简介 (不再半截话)
        if wiki_top and wiki_raw.get(wiki_top.path):
            content = wiki_raw[wiki_top.path]
            if wiki_top.citations:
                heading = find_heading(content, wiki_top.snippet)
                if heading:
                    wiki_top.citations[0].heading_path = heading
                wiki_top.citations[0].excerpt = _sentence_slice(
                    _strip_frontmatter(content), 800
                )
            wiki_top.snippet = _snippet_around(content, wiki_top.snippet)
        # RAG 侧展示文本: 清洗后给足 2000 字符 (完整为主, 不再 300 残段)
        # 再过弱模型归一化: LightRAG 的 hybrid 回答同样可能带思维步骤
        rag_display = m2_consistency.normalize_summary(
            _clean_rag_display(rag_answer, 2000)
        )
        # 其余 wiki 命中的完整呈现 (title/path/snippet/content)
        wiki_more = [
            {"title": h.title, "path": h.path, "snippet": h.snippet,
             "content": wiki_contents.get(h.path, "")}
            for h in wiki_hits[1:] if h.path
        ]
        if wiki_top is None and not rag_answer:
            if chat_answer:
                resp.results.append({
                    "kind": "reference", "name": "llm-wiki chat 参考",
                    "snippet": _sentence_slice(chat_answer, 2000),
                    "provenance": ["wiki-chat"], "confidence": "degraded",
                })
                resp.notes.append("两引擎无召回, 仅返回 chat 参考回答")
            else:
                resp.notes.append("两引擎均无召回")
            return

        # ── 结论块: M2 整合 (Q2 特点: wiki 结论 + rag 结论 LLM 合并) ──
        # 弱模型恒走 py-llm-wiki chat 形态 (助手角色 + 编号资料 + 极简问答,
        # 同模型下 chat 输出正常而"合并器"角色诱发 CoT 的教训);
        # 二次提炼 prompt (extract_conclusion) 已删 —— 整合输出只过
        # 文字层归一化。整合失败/超时 → chat 参考回答直出降级 (绝不空窗)。
        t_int = time.monotonic()
        project_context = ""
        try:
            ctx_parts = []
            for fname in ("purpose.md", "schema.md"):
                fp = Path(self.project_path) / fname
                if fp.exists():
                    ctx_parts.append(fp.read_text(encoding="utf-8")[:600])
            project_context = "\n".join(ctx_parts)
        except Exception:
            pass
        # 整合预算: 剩余等待上限 (保底 5s); 无 budget (MCP 直调) 时封顶 180s
        integrate_budget = None
        if budget and budget > 0:
            integrate_budget = max(budget - (time.monotonic() - t_mech0), 5.0)
        else:
            integrate_budget = 180.0
        merged = None
        merge_fail = ""  # 降级原因 (超时/异常/无输出)
        try:
            merged = await asyncio.wait_for(
                m2_consistency.compare_mechanism(
                    wiki_claim=(wiki_content or (
                        f"{wiki_top.title}: {wiki_top.snippet}" if wiki_top else "")),
                    wiki_source=wiki_top.path if wiki_top else "",
                    rag_claim=(rag_display or rag_answer[:600]),
                    rag_source="lightrag",
                    config=self.config,
                    chat_answer=chat_answer,
                    weak=True,  # 弱模型恒走 chat 形态 (chat 链已验证质量好)
                    query=query,
                    project_context=project_context,
                ),
                timeout=integrate_budget,
            )
        except asyncio.TimeoutError:
            merge_fail = "等待上限内未完成"
        except Exception as e:
            logger.warning("M2 整合失败: %s", e)
            merge_fail = "LLM 不可用"
        resp.timings["整合"] = time.monotonic() - t_int

        # 结论出处: 与整合 prompt 的编号材料一一对应 ([1] wiki 原文 /
        # [2] lightrag / [3] chat 参考) —— 正文角标可对回原文, 结论不再
        # 是无出处的孤立文章 (内网实调: 角标 [1][2][3] 无处可查)
        concl_citations: list[dict] = []
        if wiki_top and wiki_top.path:
            concl_citations.append({
                "ref_no": 1, "source": "wiki", "path": wiki_top.path,
                "excerpt": _sentence_slice(
                    _strip_frontmatter(wiki_raw.get(wiki_top.path, "")), 300),
            })
        for c in rag_citations[:3]:
            concl_citations.append({**c.to_dict(), "ref_no": 2})
        for ref in (chat_refs or [])[:5]:
            if ref.get("path"):
                concl_citations.append({
                    "ref_no": 3, "source": "wiki-chat", "path": ref["path"],
                    "excerpt": _sentence_slice(
                        _strip_frontmatter(ref.get("snippet") or ""), 300),
                })

        if merged:
            resp.results.append({
                "kind": "summary",
                "name": "结论 (M2 整合)",
                "text": _sentence_slice(m2_consistency.normalize_summary(merged), 8000),
                "provenance": ["M2"],
                "citations": concl_citations,
            })
            resp.notes.append("M2整合: ok")
            logger.info("M2 整合结论 head: %s", merged[:400].replace("\n", " "))
            # ── llm-wiki chat 完整结论独立块 (内网实调定调: 最终结论之上
            #    多一个 wiki 结论 —— chat 输出 + 引用页, 可折叠但不能没有) ──
            if chat_answer:
                resp.results.append({
                    "kind": "wiki_chat",
                    "name": "llm-wiki chat 结论",
                    "text": _sentence_slice(
                        m2_consistency.normalize_summary(chat_answer), 8000
                    ),
                    "provenance": ["wiki-chat"],
                    "references": [
                        {
                            "title": r.get("title") or r.get("path", ""),
                            "path": r.get("path", ""),
                            "snippet": (r.get("snippet") or "")[:500],
                        }
                        for r in (chat_refs or [])
                    ],
                })
        elif chat_answer:
            resp.results.append({
                "kind": "summary",
                "name": "结论 (llm-wiki chat)",
                "text": _sentence_slice(m2_consistency.normalize_summary(chat_answer), 8000),
                "provenance": ["wiki-chat"],
                "citations": concl_citations,
            })
            resp.notes.append(
                f"M2整合: {merge_fail or '无输出'}, 结论用 chat 参考回答"
            )
            logger.info("chat 直出结论 head: %s", chat_answer[:400].replace("\n", " "))
        else:
            resp.notes.append(
                "chat 超时降级: 结论块空缺, llm-wiki 原文证据照常 (超时也出结果)"
                if resp.timed_out
                else "整合与 chat 均不可用, 结论块空缺 (证据块照常)"
            )

        # ── 主形态: 物理分离的双引擎证据 (零 LLM 依赖, 永远完整) ──
        if wiki_top:
            resp.results.append({
                **wiki_top.to_dict(),
                "kind": "wiki_evidence",
                "provenance": ["wiki"],
                "content": wiki_content,
                "wiki_more": wiki_more,
            })
        if rag_answer:
            resp.results.append({
                "kind": "rag_evidence",
                "name": "RAG 原文证据",
                "snippet": rag_display,
                "provenance": ["rag"],
                "citations": [c.to_dict() for c in rag_citations],
            })
