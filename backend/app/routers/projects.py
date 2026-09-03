"""项目注册端点。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import session_scope
from ..models import Project
from ..repos import list_projects

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    id: str
    path: str
    wiki_project_id: str = ""   # llm-wiki 侧项目 id (默认同 id)
    rag_workdir: str = ""       # LightRAG 工作目录覆盖
    wiki_base: str = ""
    alias_mode: str = "l2+l3"
    rag_language: str = "Simplified Chinese"
    rag_entity_guidance: str = ""
    aliases_file: str = "kb-aliases.yaml"


@router.get("")
async def projects() -> list[dict]:
    rows = await list_projects()
    return [
        {
            "id": r.id, "path": r.path, "wiki_base": r.wiki_base,
            "alias_mode": r.alias_mode, "aliases_file": r.aliases_file,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]


@router.post("")
async def create_project(p: ProjectCreate) -> dict:
    from pathlib import Path

    if not Path(p.path).exists():
        raise HTTPException(status_code=400, detail=f"项目路径不存在: {p.path}")
    async with session_scope() as s:
        row = await s.get(Project, p.id)
        if row is not None:
            raise HTTPException(status_code=409, detail=f"项目 {p.id} 已注册")
        s.add(Project(
            id=p.id, path=str(Path(p.path).resolve()),
            wiki_project_id=p.wiki_project_id or p.id,
            rag_workdir=p.rag_workdir,
            wiki_base=p.wiki_base, alias_mode=p.alias_mode,
            rag_language=p.rag_language,
            rag_entity_guidance=p.rag_entity_guidance,
            aliases_file=p.aliases_file,
        ))
    return {"ok": True, "id": p.id}
