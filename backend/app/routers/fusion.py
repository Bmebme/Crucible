"""融合查询端点: 复用 core 的 FusionOrchestrator, 输出统一契约。"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import session_scope
from ..models import QueryLog
from ..services.engines import get_orchestrator

router = APIRouter(prefix="/fusion", tags=["fusion"])


class QueryRequest(BaseModel):
    query: str
    project_id: str = "current"
    history: list[str] = Field(default_factory=list)
    env: str = ""
    alias_mode: str | None = None


class EnumRequest(BaseModel):
    hint: str
    project_id: str = "current"
    alias_mode: str | None = None


class ExperienceRequest(BaseModel):
    query: str
    project_id: str = "current"
    env: str = ""
    history: list[str] = Field(default_factory=list)


async def _project_of(project_id: str):
    """项目行: 从 projects 表读, 未注册时抛 404。"""
    from ..repos import get_project

    return await get_project(project_id)


async def _log_query(project_id: str, query: str, qtype: str, alias_mode: str,
                     rewritten_to: str, latency_ms: float, result_kind: str) -> None:
    try:
        async with session_scope() as s:
            s.add(QueryLog(
                project_id=project_id, query=query, qtype=qtype,
                alias_mode=alias_mode, rewritten_to=rewritten_to,
                latency_ms=latency_ms, result_kind=result_kind,
            ))
    except Exception:
        pass  # 审计失败不影响查询


@router.post("/query")
async def fusion_query(req: QueryRequest) -> dict:
    t0 = time.monotonic()
    proj = await _project_of(req.project_id)
    project_path = proj.path
    orch = await get_orchestrator(
        req.project_id, project_path,
        wiki_project_id=proj.wiki_project_id,
        rag_workdir=proj.rag_workdir,
    )
    if req.alias_mode:
        orch.config.alias_mode = req.alias_mode
    try:
        resp = await orch.run(req.query, env=req.env, history=req.history or None)
    except Exception as e:  # 引擎级异常兜底, 不向外抛栈
        raise HTTPException(status_code=500, detail=f"fusion error: {e}") from e
    data = resp.to_dict()
    await _log_query(
        req.project_id, req.query, str(data.get("routing", {}).get("query_type", "")),
        orch.config.alias_mode,
        next((n for n in resp.notes if n.startswith("rewritten_to=")), ""),
        (time.monotonic() - t0) * 1000, "query",
    )
    try:
        from ..services import ledger as ledger_svc

        await ledger_svc.save_conflicts(req.project_id, req.query, data.get("conflicts", []))
    except Exception:
        pass
    return data


@router.post("/enum")
async def fusion_enum(req: EnumRequest) -> dict:
    t0 = time.monotonic()
    proj = await _project_of(req.project_id)
    project_path = proj.path
    orch = await get_orchestrator(
        req.project_id, project_path,
        wiki_project_id=proj.wiki_project_id,
        rag_workdir=proj.rag_workdir,
    )
    if req.alias_mode:
        orch.config.alias_mode = req.alias_mode
    try:
        resp = await orch.run_enum(req.hint)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"fusion error: {e}") from e
    data = resp.to_dict()
    await _log_query(
        req.project_id, f"enum:{req.hint}", "Q1", orch.config.alias_mode, "",
        (time.monotonic() - t0) * 1000, "enum",
    )
    # P5 落账: 差异清单 + L3 判定对进审核队列 (失败不影响查询)
    try:
        from ..services import ledger as ledger_svc

        await ledger_svc.save_enum_artifacts(
            req.project_id, f"enum:{req.hint}",
            data.get("differences", []), data.get("notes", []),
        )
    except Exception:
        pass
    return data


@router.post("/experience")
async def fusion_experience(req: ExperienceRequest) -> dict:
    t0 = time.monotonic()
    proj = await _project_of(req.project_id)
    project_path = proj.path
    orch = await get_orchestrator(
        req.project_id, project_path,
        wiki_project_id=proj.wiki_project_id,
        rag_workdir=proj.rag_workdir,
    )
    try:
        resp = await orch.run(req.query, env=req.env, history=req.history or None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"fusion error: {e}") from e
    data = resp.to_dict()
    await _log_query(
        req.project_id, req.query, str(data.get("routing", {}).get("query_type", "")),
        orch.config.alias_mode,
        next((n for n in resp.notes if n.startswith("rewritten_to=")), ""),
        (time.monotonic() - t0) * 1000, "experience",
    )
    try:
        from ..services import ledger as ledger_svc

        await ledger_svc.save_conflicts(req.project_id, req.query, data.get("conflicts", []))
    except Exception:
        pass
    return data
