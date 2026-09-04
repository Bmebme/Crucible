"""DocReader 服务: 二进制文档 → Markdown (P3)。

MinerU 优先 (4.x CLI), 未安装/失败时 markitdown 兜底。
模型: ModelScope 下载, 缓存到 /models volume (首次解析慢, 之后快)。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("docreader")

app = FastAPI(title="Crucible DocReader", version="0.1.0")

_MINERU = shutil.which("mineru")
_MARKITDOWN = False
try:
    from markitdown import MarkItDown  # noqa: F401

    _MARKITDOWN = True
except ImportError:
    pass

MAX_BYTES = 50 * 1024 * 1024


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mineru": bool(_MINERU),
        "markitdown": _MARKITDOWN,
    }


@app.post("/parse")
async def parse(file: UploadFile = File(...)) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    if len(content) > MAX_BYTES:
        raise HTTPException(413, "超过 50MB 限制")

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"input{suffix or '.bin'}"
        src.write_bytes(content)

        text = ""
        engine = ""
        if _MINERU:
            out = Path(td) / "out"
            try:
                proc = subprocess.run(
                    ["mineru", "-p", str(src), "-o", str(out)],
                    capture_output=True, text=True, timeout=900,
                )
                mds = list((out / src.stem).glob("*.md")) if (out / src.stem).exists() else []
                if proc.returncode == 0 and mds:
                    text = mds[0].read_text(encoding="utf-8")
                    engine = "mineru"
            except Exception as e:  # 超时/崩溃 → 兜底
                logger.warning("mineru failed: %s", e)

        if not text and _MARKITDOWN:
            try:
                from markitdown import MarkItDown

                result = MarkItDown().convert(str(src))
                text = result.text_content or ""
                engine = "markitdown"
            except Exception as e:
                logger.warning("markitdown failed: %s", e)

        if not text:
            raise HTTPException(422, f"解析失败 (mineru={'ok' if _MINERU else 'no'}, markitdown={'ok' if _MARKITDOWN else 'no'})")

        return {"ok": True, "filename": file.filename, "engine": engine, "markdown": text}
