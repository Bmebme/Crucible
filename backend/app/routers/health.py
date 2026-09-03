"""健康探针: 区分进程活 / 引擎可用 (engineering-plan §5.6)。"""
from __future__ import annotations

import httpx

from fastapi import APIRouter

from ..config import get_settings
from ..services.engines import get_wiki

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    wiki_ok = False
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as c:
            r = await c.get(f"{s.wiki_base}/health")
            wiki_ok = r.status_code == 200
    except Exception:
        wiki_ok = False

    rag = "unknown"
    # rag 初始化较重, 探针不触发初始化, 仅报告单例缓存状态
    from ..services.engines import _rag_by_workdir

    if not _rag_by_workdir:
        rag = "cold"
    else:
        ready = sum(1 for e in _rag_by_workdir.values() if e._ready)
        rag = "ready" if ready else "initializing"

    status = "ok" if wiki_ok else "degraded"
    return {
        "status": status,
        "wiki": {"ok": wiki_ok, "base": s.wiki_base},
        "rag": rag,
        "llm": {"model": s.llm_model, "configured": bool(s.llm_api_key)},
    }
