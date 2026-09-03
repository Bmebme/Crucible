"""文档上传端点 (md/txt 双通道, P3a)。"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..repos import get_project_path
from ..services.ingestion import ingest_document, list_jobs

router = APIRouter(prefix="/projects", tags=["documents"])


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
