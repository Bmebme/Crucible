"""LightRAG 实体图引擎适配 (demo 级)。

设计文档 §4: 实体枚举 (按类型全量拉出) 与多跳检索。当前实现走
LightRAG 的 local/hybrid 查询取结构化实体 JSON —— 内部 demo 版本
上线后按同样接口替换为内网部署的 LightRAG server。

要求: pip install "lightrag-hku[api]" + 本地嵌入模型
(CRUCIBLE_EMBED_MODEL, 默认 BAAI/bge-small-zh-v1.5)。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

# HuggingFace 直连不稳定 —— 走国内镜像 (与 llm_wiki 调试约定一致)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from ..config import Config
from ..schemas import RagEntity

_ENTITY_LINE_RE = re.compile(r'\{"entity"\s*:\s*"([^"]+)"\s*,\s*"type"\s*:\s*"([^"]+)"')


class RagEngine:
    """惰性加载 LightRAG; 未安装或未初始化时全部方法降级为空结果。"""

    def __init__(self, config: Config):
        self.config = config
        self._rag: Any = None
        self._ready = False
        self._workdir = ""

    async def ensure_ready(self, project_path: str) -> bool:
        if self._ready and self._workdir == self.config.rag_workdir_for(project_path):
            return True
        self._workdir = self.config.rag_workdir_for(project_path)
        try:
            from lightrag import LightRAG, QueryParam
            from lightrag.llm.openai import openai_complete_if_cache
            from lightrag.utils import EmbeddingFunc

            cfg = self.config

            async def llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs):
                return await openai_complete_if_cache(
                    cfg.llm_model,
                    prompt,
                    system_prompt=system_prompt,
                    history_messages=history_messages or [],
                    api_key=cfg.llm_api_key,
                    base_url=cfg.llm_base,
                    **kwargs,
                )

            from sentence_transformers import SentenceTransformer

            st_model = SentenceTransformer(cfg.embed_model)

            async def embedding_func(texts: list[str]):
                return st_model.encode(texts, normalize_embeddings=True)

            self._rag = LightRAG(
                working_dir=self._workdir,
                llm_model_func=llm_model_func,
                embedding_func=EmbeddingFunc(
                    embedding_dim=cfg.embed_dim,
                    max_token_size=512,
                    func=embedding_func,
                ),
                # L1 跨语言对齐 (设计文档 §5.5): 中文语料 → 中文实体名,
                # 与 wiki 中文页面名同语言, M1 字符串归一化即可命中。
                addon_params={"language": cfg.rag_language},
            )
            await self._rag.initialize_storages()
            self._QueryParam = QueryParam
            self._ready = True
            return True
        except Exception:
            self._ready = False
            return False

    async def enumerate_entities(self, project_path: str, hint: str) -> list[RagEntity]:
        """实体枚举 (Q1 全通道)。以 local 模式取结构化实体 JSON。"""
        if not await self.ensure_ready(project_path):
            return []
        prompt = f"列出文档中所有与「{hint}」相关的实体（组件/接口/服务/概念），逐条输出 JSON 实体清单"
        try:
            result = await self._rag.aquery(
                prompt,
                param=self._QueryParam(
                    mode="local", only_need_context=True, enable_rerank=False
                ),
            )
        except Exception:
            return []
        entities: list[RagEntity] = []
        seen: set[str] = set()
        for m in _ENTITY_LINE_RE.finditer(str(result)):
            name, etype = m.group(1), m.group(2)
            if name in seen:
                continue
            seen.add(name)
            entities.append(RagEntity(name=name, entity_type=etype))
        return entities

    async def query(self, project_path: str, text: str, mode: str = "hybrid") -> str:
        """机制型/链式查询 (Q2)。"""
        if not await self.ensure_ready(project_path):
            return ""
        try:
            result = await self._rag.aquery(
                text,
                param=self._QueryParam(mode=mode, enable_rerank=False),
            )
            return str(result)
        except Exception:
            return ""

    async def ingest(self, project_path: str, text: str) -> bool:
        if not await self.ensure_ready(project_path):
            return False
        try:
            await self._rag.ainsert(text)
            return True
        except Exception:
            return False
