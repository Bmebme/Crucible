"""上传摄入管线 (md/txt 双通道, P3a)。

通道 A (wiki):  原子写入 <project>/wiki/ 子目录, llm-wiki daemon watcher 索引
通道 B (rag):   RagEngine.ingest → LightRAG ainsert
状态跟踪:       ingestion_jobs 表 (uploaded → wiki_indexed → rag_ingested → done)
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..db import session_scope
from ..models import IngestionJob
from .engines import get_rag

ALLOWED_EXT = {".md", ".txt"}

WIKI_SUBDIR = {
    "verification": "verification",  # 验证记录模板 (frontmatter 带 verify_state)
}


async def ingest_document(
    project_id: str,
    project_path: str,
    filename: str,
    content: bytes,
    subdir: str = "",
) -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return {
            "ok": False,
            "error": f"格式 {ext} 暂不支持 (P3 MinerU 管线后支持 pdf/docx); 当前支持 {sorted(ALLOWED_EXT)}",
        }

    # 1. 建任务
    async with session_scope() as s:
        job = IngestionJob(project_id=project_id, filename=filename, kind=ext[1:], status="uploaded")
        s.add(job)
        await s.flush()
        job_id = job.id

    try:
        # 2. 通道 A: 原子写入 wiki 目录 (临时文件 + rename, 防 watcher 半写竞态)
        wiki_root = Path(project_path) / "wiki"
        target_dir = wiki_root / (WIKI_SUBDIR.get(subdir, "") or "")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        text = content.decode("utf-8", errors="replace")
        fd, tmp = tempfile.mkstemp(dir=str(target_dir), suffix=ext)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, target)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        wiki_path = str(Path("wiki") / (WIKI_SUBDIR.get(subdir, "") or "") / filename)

        await _update_job(job_id, status="wiki_indexed", wiki_path=wiki_path)

        # 3. 通道 B: rag 摄入 (纯文本; source_name 写入 reference 列表供引用层锚定)
        rag = await get_rag(project_path)
        ok = await rag.ingest(project_path, text, source_name=filename)
        if not ok:
            await _update_job(
                job_id, status="failed", error="rag 摄入失败 (引擎未就绪或 ainsert 失败)"
            )
            return {"ok": False, "job_id": job_id, "error": "rag 摄入失败"}

        await _update_job(
            job_id, status="done", detail={"chars": len(text), "channels": ["wiki", "rag"]}
        )
        return {"ok": True, "job_id": job_id, "wiki_path": wiki_path}
    except Exception as e:
        await _update_job(job_id, status="failed", error=str(e))
        return {"ok": False, "job_id": job_id, "error": str(e)}


async def _update_job(job_id: int, **fields) -> None:
    async with session_scope() as s:
        job = await s.get(IngestionJob, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)


async def list_jobs(project_id: str, limit: int = 50) -> list[dict]:
    from sqlalchemy import select

    async with session_scope() as s:
        rows = (
            await s.scalars(
                select(IngestionJob)
                .where(IngestionJob.project_id == project_id)
                .order_by(IngestionJob.id.desc())
                .limit(limit)
            )
        ).all()
    return [
        {
            "id": r.id, "filename": r.filename, "kind": r.kind,
            "status": r.status, "wiki_path": r.wiki_path,
            "error": r.error, "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]
