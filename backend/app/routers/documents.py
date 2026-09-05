"""文档上传端点 (md/txt 双通道, P3a) + 页面原文代理 (引用层 P2)。

上传异步化: POST 立即建任务返回 job_id, 管线经 BackgroundTasks 执行,
前端轮询 GET /documents 看阶段 (detail.stages 各阶段时间戳)。
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from ..repos import get_project, get_project_path
from ..services.engines import get_wiki
from ..services.ingestion import (
    list_jobs,
    run_ingestion,
    start_ingestion,
    validate_upload,
)

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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    subdir: str = Form(""),
) -> dict:
    filename = file.filename or "unnamed.md"
    project_path = await get_project_path(project_id)
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    err = validate_upload(filename, content)
    if err:
        raise HTTPException(status_code=400, detail=err)

    started = await start_ingestion(project_id, filename)
    background_tasks.add_task(
        run_ingestion,
        started["job_id"], project_id, project_path, filename, content, subdir,
    )
    return {
        "ok": True,
        "job_id": started["job_id"],
        "message": "任务已接收; 轮询 GET /projects/{id}/documents 查看阶段",
    }


@router.get("/{project_id}/documents")
async def document_jobs(project_id: str) -> list[dict]:
    return await list_jobs(project_id)
