"""项目注册端点。"""
from __future__ import annotations

from pathlib import Path

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
    related_projects: list[str] = []  # 软隔离关联产品
    wiki_sync: bool = True      # 自动在 llm-wiki 侧创建项目并回填稳定 uuid
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


async def _sync_wiki_project(
    p: ProjectCreate,
) -> str:
    """在 llm-wiki 侧同步创建项目 (llm-wiki 负责建目录+模板含 schema.md),
    返回稳定 uuid。失败返回空串 (降级为手动桥接流程)。"""
    import httpx

    from ..config import get_settings

    s = get_settings()
    name = Path(p.path).name
    wiki_path = f"{s.llm_wiki_projects_root}/{name}"
    import logging as _l

    _log = _l.getLogger("crucible")
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as c:
            r = await c.post(
                f"{s.wiki_base}/api/v1/projects/create",
                json={"name": name, "path": s.llm_wiki_projects_root},
            )
            if r.status_code == 400:
                # 目录已存在 (可能是预先放好数据的场景): 走 open
                pass
            elif r.status_code != 200:
                _log.warning("wiki_sync create 失败: status=%s body=%s", r.status_code, r.text[:200])
                return ""
            r2 = await c.post(
                f"{s.wiki_base}/api/v1/projects/open",
                json={"path": wiki_path},
            )
            data = r2.json()
            if data.get("ok"):
                return str(data.get("id", ""))
            _log.warning("wiki_sync open 失败: body=%s", str(data)[:200])
    except Exception as e:
        _log.warning("wiki_sync 异常: %s", e)
        return ""
    return ""


@router.post("")
async def create_project(p: ProjectCreate) -> dict:
    wiki_project_id = p.wiki_project_id or p.id
    wiki_synced = False

    # 目录前提: 已存在 (预放数据) 或 wiki_sync 由 llm-wiki 建 (父目录须存在)
    if not Path(p.path).exists():
        if not (p.wiki_sync and Path(p.path).parent.exists()):
            raise HTTPException(
                status_code=400,
                detail=f"项目路径不存在且无法创建: {p.path} (父目录须存在)",
            )
        # llm-wiki 同步创建会建目录

    if p.wiki_sync and not p.wiki_project_id:
        synced = await _sync_wiki_project(p)
        if synced:
            wiki_project_id = synced
            wiki_synced = True

    async with session_scope() as s:
        row = await s.get(Project, p.id)
        if row is not None:
            raise HTTPException(status_code=409, detail=f"项目 {p.id} 已注册")
        s.add(Project(
            id=p.id, path=str(Path(p.path).resolve()),
            wiki_project_id=wiki_project_id,
            rag_workdir=p.rag_workdir,
            related_projects=p.related_projects,
            wiki_base=p.wiki_base, alias_mode=p.alias_mode,
            rag_language=p.rag_language,
            rag_entity_guidance=p.rag_entity_guidance,
            aliases_file=p.aliases_file,
        ))
    return {"ok": True, "id": p.id, "wiki_project_id": wiki_project_id,
            "wiki_synced": wiki_synced}
