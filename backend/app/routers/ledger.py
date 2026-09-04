"""台账与审核队列端点 (P5)。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ..repos import get_project
from ..services import ledger

router = APIRouter(prefix="/projects", tags=["ledger"])


class ResolveRequest(BaseModel):
    action: str  # approve|reject (alias) / resolve (diff)
    resolution: str = ""  # conflict: wiki|rag|both|none
    note: str = ""


@router.get("/{project_id}/ledger/diffs")
async def diffs(project_id: str, limit: int = 100) -> list[dict]:
    return await ledger.list_diffs(project_id, limit)


@router.get("/{project_id}/ledger/conflicts")
async def conflicts(project_id: str, limit: int = 50) -> list[dict]:
    return await ledger.list_conflicts(project_id, limit)


@router.get("/{project_id}/alias-reviews")
async def alias_reviews(project_id: str, status: str = "pending") -> list[dict]:
    return await ledger.list_alias_reviews(project_id, status)


@router.get("/{project_id}/aliases/file")
async def aliases_file(project_id: str) -> dict:
    """kb-aliases.yaml 当前内容 (对齐管理页展示用)。"""
    from pathlib import Path

    proj = await get_project(project_id)
    p = Path(proj.path) / (proj.aliases_file or "kb-aliases.yaml")
    if not p.exists():
        return {"exists": False, "content": ""}
    return {"exists": True, "content": p.read_text(encoding="utf-8")}


@router.get("/{project_id}/graph")
async def entity_graph(project_id: str, top_n: int = 80) -> dict:
    """实体图 (LightRAG graphml → 前端可视化)。按度数取 top_n 节点。"""
    from pathlib import Path

    import networkx as nx

    proj = await get_project(project_id)
    wd = Path(proj.rag_workdir) if proj.rag_workdir else Path(proj.path) / ".lightrag"
    gml = wd / "graph_chunk_entity_relation.graphml"
    if not gml.exists():
        return {"nodes": [], "edges": []}
    g = nx.read_graphml(str(gml))
    # 按度数取中心子图
    top = sorted(g.degree, key=lambda d: d[1], reverse=True)[:top_n]
    keep = {n for n, _ in top}
    nodes = [
        {"id": str(n), "type": str(a.get("entity_type", "")),
         "description": str(a.get("description", ""))[:120],
         "degree": g.degree(n)}
        for n, a in g.nodes(data=True) if n in keep
    ]
    edges = [
        {"s": str(u), "t": str(v)}
        for u, v in g.edges() if u in keep and v in keep
    ]
    return {"nodes": nodes, "edges": edges, "total_nodes": g.number_of_nodes()}


@router.post("/{project_id}/alias-reviews/{review_id}/resolve")
async def resolve_alias(project_id: str, review_id: int, req: ResolveRequest) -> dict:
    if req.action not in ("approve", "reject"):
        return {"ok": False, "error": "action 须为 approve|reject"}
    proj = await get_project(project_id)
    return await ledger.resolve_alias_review(
        project_id, review_id, req.action, proj.path, proj.aliases_file or "kb-aliases.yaml"
    )


@router.post("/{project_id}/ledger/diffs/{diff_id}/resolve")
async def resolve_diff(project_id: str, diff_id: int, req: ResolveRequest) -> dict:
    return await ledger.resolve_diff(project_id, diff_id, req.action, req.note)


@router.post("/{project_id}/ledger/conflicts/{conflict_id}/resolve")
async def resolve_conflict(project_id: str, conflict_id: int, req: ResolveRequest) -> dict:
    return await ledger.resolve_conflict(project_id, conflict_id, req.resolution, req.note)
