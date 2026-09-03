"""引擎单例: LightRAG 每工作目录只初始化一次 (模型加载 3.6s → 进程内一次)。

复用 core 的 crucible 包 (src/crucible, editable 安装), 把服务端 Settings
映射到 core Config。内网 LightRAG 上线后: 替换 RagEngine 为 HTTP 客户端,
此处对外接口不变。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from crucible.config import Config as CoreConfig
from crucible.engines.rag_engine import RagEngine
from crucible.engines.wiki_engine import WikiEngine
from crucible.orchestrator import FusionOrchestrator

from ..config import get_settings

_lock = asyncio.Lock()
_rag_by_workdir: dict[str, RagEngine] = {}
_wiki_by_base: dict[str, WikiEngine] = {}


def _core_config() -> CoreConfig:
    s = get_settings()
    return CoreConfig(
        wiki_base=s.wiki_base,
        llm_base=s.llm_base,
        llm_api_key=s.llm_api_key,
        llm_model=s.llm_model,
        embed_model=s.embed_model,
        embed_dim=s.embed_dim,
        rag_language=s.rag_language,
        rag_entity_guidance=s.rag_entity_guidance,
        alias_mode=s.alias_mode,
        aliases_file=s.aliases_file,
    )


def get_wiki() -> WikiEngine:
    s = get_settings()
    if s.wiki_base not in _wiki_by_base:
        _wiki_by_base[s.wiki_base] = WikiEngine(s.wiki_base)
    return _wiki_by_base[s.wiki_base]


def workdir_for(project_path: str, rag_workdir: str = "") -> str:
    s = get_settings()
    if rag_workdir:
        return rag_workdir
    if s.rag_workdir_root:
        return str(Path(s.rag_workdir_root) / Path(project_path).name)
    return str(Path(project_path) / ".lightrag")


async def get_rag(project_path: str, rag_workdir: str = "") -> RagEngine:
    """惰性初始化 + 缓存 (每工作目录一个实例, 并发安全)。"""
    wd = workdir_for(project_path, rag_workdir)
    if wd in _rag_by_workdir:
        return _rag_by_workdir[wd]
    async with _lock:
        if wd in _rag_by_workdir:
            return _rag_by_workdir[wd]
        cfg = _core_config()
        cfg.rag_workdir = wd
        eng = RagEngine(cfg)
        ok = await eng.ensure_ready(project_path)
        _rag_by_workdir[wd] = eng if ok else eng  # 失败也缓存, 下次 ensure_ready 重试
        return eng


async def get_orchestrator(
    project_id: str,
    project_path: str,
    wiki_project_id: str = "",
    rag_workdir: str = "",
) -> FusionOrchestrator:
    """每请求构造编排器 (轻量), 引擎实例复用单例 (rag 按工作目录缓存)。

    wiki_project_id: llm-wiki 侧的项目 id (本系统 id 与 llm-wiki id 可不同)。
    """
    orch = FusionOrchestrator(
        _core_config(), project_id=project_id, project_path=project_path
    )
    orch.wiki = get_wiki()
    orch.rag = await get_rag(project_path, rag_workdir)
    if wiki_project_id:
        orch.project_id = wiki_project_id  # wiki 引擎调用用 llm-wiki 侧 id
    return orch


async def rag_ready(project_path: str) -> bool:
    eng = await get_rag(project_path)
    return eng is not None and await eng.ensure_ready(project_path)
