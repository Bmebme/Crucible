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
from .routers import documents, fusion, health, ledger, projects

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
    if _mcp_app is not None:
        # mcp streamable_http_app 自带 lifespan (session_manager.run()
        # 初始化任务组), 路由提升后必须手动嵌套, 否则请求时报
        # "Task group is not initialized"
        async with _mcp_app.router.lifespan_context(_mcp_app):
            yield
    else:
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
app.include_router(ledger.router)

# MCP HTTP 端点 (Streamable HTTP): 客户端连 http://<host>:8080/mcp
# 经验: Mount 对纯函数/Starlette app 的匹配行为不可靠, 直接把内层
# /mcp 路由的 endpoint 提升注册到主应用最前面 (静态挂载之前)
_mcp_app = None
try:
    from mcp_server import http_app as _mcp_app
    from starlette.routing import Route as _SR

    _mcp_route = next(r for r in _mcp_app.routes if r.path == "/mcp")
    app.router.routes.insert(
        0, _SR("/mcp", _mcp_route.endpoint, methods=["GET", "POST", "DELETE"])
    )
except ImportError:
    pass  # mcp 未安装时跳过 (server extra 已含 mcp<2)

# 前端静态资源 (P4 构建产物; 未构建时目录不存在, 忽略)
_static = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _static.exists():
    from fastapi.responses import FileResponse

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """SPA history 路由回退: 刷新 /upload 等前端路径时浏览器直接向
        后端要该路径, 静态挂载只认真实文件 → 404。未知路径回退到
        index.html 由 Vue Router 接管 (内网实调: 刷新页面 Not Found)。"""
        candidate = (_static / full_path).resolve()
        try:
            candidate.relative_to(_static.resolve())
            is_inside = True
        except ValueError:
            is_inside = False
        if is_inside and full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_static / "index.html")

    app.mount("/", StaticFiles(directory=str(_static), html=True), name="frontend")
