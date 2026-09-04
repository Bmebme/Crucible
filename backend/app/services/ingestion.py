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
# 二进制格式: docreader 转换 (MinerU/markitdown) → md 后走同一双通道
BINARY_EXT = {".pdf", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg"}

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
    is_binary = ext in BINARY_EXT
    if ext not in ALLOWED_EXT and not is_binary:
        return {
            "ok": False,
            "error": f"格式 {ext} 不支持; 支持 {sorted(ALLOWED_EXT | BINARY_EXT)}",
        }

    # 1. 建任务
    async with session_scope() as s:
        job = IngestionJob(
            project_id=project_id, filename=filename,
            kind=ext[1:], status="uploaded",
        )
        s.add(job)
        await s.flush()
        job_id = job.id

    try:
        # 1.5 二进制格式: docreader 转换 (MinerU/markitdown)
        convert_engine = ""
        if is_binary:
            md_text, convert_engine = await _convert_via_docreader(filename, content)
            if md_text is None:
                await _update_job(job_id, status="failed", error="docreader 转换失败")
                return {"ok": False, "job_id": job_id, "error": "docreader 转换失败"}
            text = md_text
            md_filename = str(Path(filename).with_suffix(".md"))
            await _update_job(job_id, status="converted",
                              detail={"engine": convert_engine})
        else:
            text = content.decode("utf-8", errors="replace")
            md_filename = filename

        # 2. 通道 A: 原子写入 wiki 目录 (临时文件 + rename, 防 watcher 半写竞态)
        wiki_root = Path(project_path) / "wiki"
        target_dir = wiki_root / (WIKI_SUBDIR.get(subdir, "") or "")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / md_filename
        fd, tmp = tempfile.mkstemp(dir=str(target_dir), suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, target)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        wiki_path = str(Path("wiki") / (WIKI_SUBDIR.get(subdir, "") or "") / md_filename)

        await _update_job(job_id, status="wiki_indexed", wiki_path=wiki_path)

        # 3. 通道 B: rag 摄入 (纯文本; source_name 写入 reference 列表供引用层锚定)
        rag = await get_rag(project_path)
        ok = await rag.ingest(project_path, text, source_name=md_filename)
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


async def _convert_via_docreader(
    filename: str, content: bytes
) -> tuple[str | None, str]:
    """调 docreader 服务转换二进制文档。失败返回 (None, "")。"""
    import httpx

    from ..config import get_settings

    base = get_settings().docreader_base
    if not base:
        return None, ""
    try:
        async with httpx.AsyncClient(timeout=960.0, trust_env=False) as c:
            resp = await c.post(
                f"{base}/parse",
                files={"file": (filename, content)},
            )
            if resp.status_code != 200:
                return None, ""
            data = resp.json()
        return data.get("markdown") or None, data.get("engine", "")
    except Exception:
        return None, ""


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
