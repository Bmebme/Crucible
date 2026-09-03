"""Crucible 融合服务入口 (P1)。

启动: uvicorn app.main:app  (backend/ 目录下)
文档: /docs (OpenAPI)
前端: 构建产物挂在 /static (P4)
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import create_tables, init_db
from .routers import documents, fusion, health, projects

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("crucible")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await create_tables()
    logger.info(
        "crucible backend up: wiki=%s llm=%s alias_mode=%s",
        get_settings().wiki_base,
        get_settings().llm_model,
        get_settings().alias_mode,
    )
    yield


app = FastAPI(
    title="Crucible Fusion",
    description="漏洞验证知识库融合层: 判别 → 双引擎召回 → M1/M2/M3 合并",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # P6 认证时收紧
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = uuid.uuid4().hex[:12]
    request.state.rid = rid
    t0 = time.monotonic()
    try:
        resp = await call_next(request)
    except Exception:
        logger.exception("unhandled error rid=%s", rid)
        return JSONResponse(
            status_code=500,
            content={"ok": False, "rid": rid, "error": "internal error"},
        )
    resp.headers["X-Request-ID"] = rid
    logger.info(
        "%s %s -> %s (%.0fms)", request.method, request.url.path,
        resp.status_code, (time.monotonic() - t0) * 1000,
    )
    return resp


app.include_router(health.router)
app.include_router(fusion.router)
app.include_router(projects.router)
app.include_router(documents.router)

# 前端静态资源 (P4 构建产物; 未构建时目录不存在, 忽略)
_static = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _static.exists():
    app.mount("/", StaticFiles(directory=str(_static), html=True), name="frontend")
