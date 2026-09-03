"""项目注册读写 (PG)。"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select

from .db import session_scope
from .models import Project


async def get_project(project_id: str) -> Project:
    async with session_scope() as s:
        row = await s.get(Project, project_id)
    if row is not None and row.path:
        return row
    raise HTTPException(
        status_code=404,
        detail=f"项目 {project_id} 未注册: 先 POST /projects 注册 (id + path)",
    )


async def get_project_path(project_id: str) -> str:
    return (await get_project(project_id)).path


async def list_projects() -> list[Project]:
    async with session_scope() as s:
        rows = await s.scalars(select(Project).order_by(Project.id))
        return list(rows)
