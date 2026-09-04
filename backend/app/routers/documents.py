"""文档上传端点 (md/txt 双通道, P3a) + 页面原文代理 (引用层 P2)。"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..repos import get_project, get_project_path
from ..services.engines import get_wiki
from ..services.ingestion import ingest_document, list_jobs

router = APIRouter(prefix="/projects", tags=["documents"])


@router.get("/{project_id}/pages/content")
async def page_content(project_id: str, path: str) -> dict:
    """wiki 页面整页原文 (引用层: 前端跳转原文用)。"""
    proj = await get_project(project_id)
    wiki = get_wiki()
    wiki_project_id = proj.wiki_project_id or project_id
    content = await wiki.read_page_content(wiki_project_id, path)
    return {"path": path, "content": content}


@router.post("/{project_id}/documents")
async def upload_document(
    project_id: str,
    file: UploadFile = File(...),
    subdir: str = Form(""),
) -> dict:
    project_path = await get_project_path(project_id)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件超过 10MB 限制")
    return await ingest_document(
        project_id, project_path, file.filename or "unnamed.md", content, subdir=subdir
    )


@router.get("/{project_id}/documents")
async def document_jobs(project_id: str) -> list[dict]:
    return await list_jobs(project_id)
