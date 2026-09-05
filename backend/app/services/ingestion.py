"""上传摄入管线 (统一源架构)。

布局约定 (2026-09-05, 与 py-llm-wiki 搜索/文件树对齐):
  raw/originals/<原名>      原始 PDF/docx 存档 (证据链; 不进树不检索)
  raw/sources/<名>.md       MinerU 标准化源 = 统一输入源 (RAG 全文 +
                            llm-wiki 树/搜索公开根, 两引擎同一源头)
  wiki/verification/        验证记录 (知识页, 原约定不变)
  <项目>/.lightrag/         RAG 索引 (派生数据)

通道 A (源写入): 原子写标准化源 (默认 raw/sources, verification 例外)
通道 B (rag):     RagEngine.ingest → LightRAG ainsert (内存传文本)
状态跟踪:       ingestion_jobs 表; status = 当前阶段, detail.stages =
                各阶段时间戳 (前端实时显示任务卡在哪一步)
阶段流:         uploaded → (converting) → converted → sourced
                → rag_ingesting → done / failed
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..db import session_scope
from ..models import IngestionJob
from .engines import get_rag

logger = logging.getLogger("crucible.ingestion")

ALLOWED_EXT = {".md", ".txt"}
# 二进制格式: docreader 转换 (MinerU/markitdown) → md 后走统一源
BINARY_EXT = {".pdf", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg"}
MAX_BYTES = 50 * 1024 * 1024  # 与 docreader 的 50MB 上限一致


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
    wiki_project_id: str = "",
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
        # 1.2 原始文档存档 (证据链): 二进制 → raw/originals; 文本直传
        #     的内容即标准化源, 不单独存原件
        originals_rel = ""
        if is_binary:
            raw_dir = Path(project_path) / "raw" / "originals"
            raw_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(raw_dir), suffix=Path(filename).suffix)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(content)
                os.replace(tmp, raw_dir / filename)
                originals_rel = f"raw/originals/{filename}"
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

        # 2. 标准化源写入 (统一输入源, RAG 与 llm-wiki 搜索共用):
        #    默认 → raw/sources/ (llm-wiki 公开根, 树+搜索覆盖)
        #    verification 子目录 → wiki/verification/ (知识页, 原约定)
        #    原子写 (临时文件 + rename, 防 watcher 半写竞态)
        if subdir == "verification":
            target_dir = Path(project_path) / "wiki" / "verification"
            source_rel = str(Path("wiki") / "verification" / md_filename)
        else:
            target_dir = Path(project_path) / "raw" / "sources"
            source_rel = str(Path("raw") / "sources" / md_filename)
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
        stages = await _set_phase(job_id, "sourced", stages, source_path=source_rel)
        await _update_job(job_id, wiki_path=source_rel)

        # 2.5 触发 llm-wiki ingest: wiki 是知识层, 原材料在 raw/sources,
        #     知识页由 llm-wiki 的管线 (LLM 抽取) 生成进 wiki 树 ——
        #     crucible 只写源 + 触发, 不直接塞 wiki (M1 搜的是生成知识页)
        wiki_note = "skipped (verification)"
        if subdir != "verification":
            wiki_note = await _enqueue_wiki_ingest(wiki_project_id, source_rel)
        if wiki_note.startswith(("ok", "skip")):
            logger.info("wiki ingest enqueue: %s (%s)", source_rel, wiki_note)
        else:
            logger.warning("wiki ingest enqueue FAILED: %s (%s)", source_rel, wiki_note)

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
                    "chars": len(text), "channels": ["source", "rag"],
                    "source_path": source_rel,
                    "raw_path": originals_rel,
                    "wiki_ingest": wiki_note},
        )
    except Exception as e:
        await _fail(str(e))


async def _enqueue_wiki_ingest(wiki_project_id: str, source_path: str) -> str:
    """触发 llm-wiki ingest 管线消费统一源 (LLM 生成知识页进 wiki 树)。

    返回 "ok" / "ok (n tasks)" / "skip (...)" / "failed (...)"。
    """
    from ..config import get_settings

    base = get_settings().wiki_base
    if not base:
        return "skip (wiki_base 未配置)"
    if not wiki_project_id:
        return "skip (项目未同步 wiki_project_id)"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as c:
            resp = await c.post(
                f"{base}/api/v1/projects/{wiki_project_id}/ingest/enqueue",
                json={"paths": [source_path]},
            )
        if resp.status_code != 200:
            return f"failed (HTTP {resp.status_code})"
        return "ok"
    except Exception as e:
        return f"failed: {e}"


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
