"""上传摄入管线 (md/txt 双通道, P3a)。

原件存档:       <project>/raw/sources/<文件名> (证据链, 转换前原子写入)
通道 A (wiki):  原子写入 <project>/wiki/ 子目录, llm-wiki daemon watcher 索引
通道 B (rag):   RagEngine.ingest → LightRAG ainsert
状态跟踪:       ingestion_jobs 表; status = 当前阶段, detail.stages =
                各阶段时间戳 (前端实时显示任务卡在哪一步)
阶段流:         uploaded → (converting) → converted → wiki_indexed
                → rag_ingesting → done / failed
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
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


def validate_upload(filename: str, content: bytes) -> str | None:
    """上传合法性检查, 返回错误文案或 None。"""
    ext = Path(filename).suffix.lower()
    is_binary = ext in BINARY_EXT
    if ext not in ALLOWED_EXT and not is_binary:
        return f"格式 {ext} 不支持; 支持 {sorted(ALLOWED_EXT | BINARY_EXT)}"
    if len(content) > MAX_BYTES:
        return f"超过 {MAX_BYTES // 1024 // 1024}MB 限制"
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def start_ingestion(project_id: str, filename: str) -> dict:
    """建任务 (阶段 uploaded), 立即返回 job_id —— 管线异步执行。"""
    async with session_scope() as s:
        job = IngestionJob(
            project_id=project_id, filename=filename,
            kind=Path(filename).suffix.lower().lstrip("."), status="uploaded",
            detail={"stages": {"uploaded": _now()}},
        )
        s.add(job)
        await s.flush()
        return {"ok": True, "job_id": job.id}


async def _set_phase(job_id: int, status: str, stages: dict, **extra) -> dict:
    """推进阶段: 更新 status + stages 时间戳, 返回新的 stages。"""
    stages = {**stages, status: _now()}
    await _update_job(job_id, status=status, detail={"stages": stages, **extra})
    return stages


async def run_ingestion(
    job_id: int,
    project_id: str,
    project_path: str,
    filename: str,
    content: bytes,
    subdir: str = "",
) -> None:
    """摄入管线 (由端点以 BackgroundTasks 异步执行; 全程阶段可见)。"""
    ext = Path(filename).suffix.lower()
    is_binary = ext in BINARY_EXT
    async with session_scope() as s:
        job = await s.get(IngestionJob, job_id)
        stages = dict((job.detail or {}).get("stages") or {}) if job else {}
    stages.setdefault("uploaded", _now())

    async def _fail(error: str) -> None:
        await _update_job(
            job_id, status="failed", error=error,
            detail={"stages": {**stages, "failed": _now()}},
        )

    try:
        # 1.2 原始文件存档 (对齐 py-llm-wiki raw/sources 约定):
        #     证据链保留原件 —— MinerU 是转换不是归档, 解析产物丢 wiki 通道,
        #     原件必须留档 (转换失败也不丢; 换解析器可重跑; 同名覆盖=最新为准)。
        raw_dir = Path(project_path) / "raw" / "sources"
        raw_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(raw_dir), suffix=Path(filename).suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.replace(tmp, raw_dir / filename)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

        # 1.5 二进制格式: docreader 转换 (MinerU/markitdown)
        if is_binary:
            stages = await _set_phase(job_id, "converting", stages)
            md_text, convert_engine = await _convert_via_docreader(filename, content)
            if md_text is None:
                await _fail("docreader 转换失败")
                return
            text = md_text
            md_filename = str(Path(filename).with_suffix(".md"))
            stages = await _set_phase(job_id, "converted", stages, engine=convert_engine)
        else:
            text = content.decode("utf-8", errors="replace")
            md_filename = filename
            stages = await _set_phase(job_id, "converted", stages)

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
        stages = await _set_phase(job_id, "wiki_indexed", stages, wiki_path=wiki_path)
        await _update_job(job_id, wiki_path=wiki_path)

        # 3. 通道 B: rag 摄入 (纯文本; source_name 写入 reference 列表供引用层锚定)
        stages = await _set_phase(job_id, "rag_ingesting", stages)
        rag = await get_rag(project_path)
        ok = await rag.ingest(project_path, text, source_name=md_filename)
        if not ok:
            await _fail("rag 摄入失败 (引擎未就绪或 ainsert 失败)")
            return

        await _update_job(
            job_id, status="done",
            detail={"stages": {**stages, "done": _now()},
                    "chars": len(text), "channels": ["wiki", "rag"],
                    "raw_path": f"raw/sources/{filename}"},
        )
    except Exception as e:
        await _fail(str(e))


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
            "error": r.error, "detail": r.detail or {},
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]
