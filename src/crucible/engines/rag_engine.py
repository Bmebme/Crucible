"""LightRAG 实体图引擎适配 (demo 级)。

设计文档 §4: 实体枚举 (按类型全量拉出) 与多跳检索。当前实现走
LightRAG 的 local/hybrid 查询取结构化实体 JSON —— 内部 demo 版本
上线后按同样接口替换为内网部署的 LightRAG server。

要求: pip install "lightrag-hku[api]" + 本地嵌入模型
(CRUCIBLE_EMBED_MODEL, 默认 BAAI/bge-small-zh-v1.5)。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

# HuggingFace 直连不稳定 —— 走国内镜像 (与 llm_wiki 调试约定一致)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def _model_is_cached(model_id: str) -> bool:
    """模型是否已在本地 HF 缓存 (目录级探测, 避免镜像抖动时加载阻塞)。"""
    try:
        from pathlib import Path

        cache_root = Path(
            os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")
        ) / "hub"
        return any(cache_root.glob(f"models--{model_id.replace('/', '--')}*"))
    except Exception:
        return False


def _prefer_offline_hub() -> None:
    """模型已缓存时强制 hub 离线。

    HF_HUB_OFFLINE 是 huggingface_hub 的导入期常量 —— env 在 hub 已被
    导入 (lightrag/transformers 链条) 之后设置不生效, 必须直接改常量。
    """
    try:
        import huggingface_hub.constants as _hc

        _hc.HF_HUB_OFFLINE = True
    except Exception:
        pass

from ..config import Config
from ..schemas import Citation, RagEntity, RagChunk

_ENTITY_LINE_RE = re.compile(
    r'\{"entity"\s*:\s*"([^"]+)"\s*,\s*"type"\s*:\s*"([^"]+)"'
    r'(?:\s*,\s*"description"\s*:\s*"([^"]*)")?'
)

# Q1 枚举噪声过滤 (提取引导之外的兜底): 日期/版本号/数值/字段名/模板泄漏。
# 注意: 这是 demo 级语料调优, 长期应由 entity_types_guidance 在源头解决。
_NOISE_RES: list[re.Pattern] = [
    re.compile(r"^\d{4}[./-]\d{1,2}"),        # 2025.1 / 2026-06-10
    re.compile(r"^\d+(\.\d+)+$"),             # 3.2.1
    re.compile(r"^\d+C\d+G$"),                # 32C128G
    re.compile(r"^\d+(个|节点|集群)"),          # 48节点 / 3集群 / 100个微服务实例
    re.compile(r"^\d+$"),                     # 纯数字
    re.compile(r"^ENTITY_START|resource_or_entity"),  # 模板泄漏
]
_NOISE_NAMES: set[str] = {
    "name", "type", "description", "definition", "definitions", "modules",
    "module_id", "role", "responsibilities", "timestamp", "vendor",
    "severity", "source_location", "sub_domains", "core_concepts",
    "domain_boundary", "domain_name", "architecture_style",
    "common_mechanisms", "tech_stack", "dependencies", "contracts",
    "data_ownership", "artifact", "job", "entity description",
    "an alarmrawmessage", "字段",
}

# 类型级噪声: guidance 约束后出现的兜底类型 (如 LLM 归类不出的"其他").
# demo 级语料调优, 由 CRUCIBLE_RAG_ENTITY_GUIDANCE 在源头解决为主。
_NOISE_TYPES: set[str] = {"其他"}


def _is_noise(name: str) -> bool:
    if name.lower() in _NOISE_NAMES:
        return True
    return any(r.search(name) for r in _NOISE_RES)


def parse_context_chunks(raw: str) -> list["RagChunk"]:
    """从 only_need_context 的上下文里解析 Document Chunks (原文引用单元)。

    LightRAG 每个 chunk 是独立一行 JSON; 按行解析, 失败行跳过。
    """
    chunks: list[RagChunk] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith('{"reference_id"'):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = str(obj.get("content", ""))
        if not content:
            continue
        key = str(obj.get("reference_id", "")) + content[:64]
        if key in seen:
            continue
        seen.add(key)
        chunks.append(
            RagChunk(
                reference_id=str(obj.get("reference_id", "")),
                content=content,
                headings=str(obj.get("content_headings", "")),
            )
        )
    return chunks


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

            # 模型已缓存: 关闭 hub 在线探测 (hf-mirror 抖动时 HEAD 重试会阻塞分钟级)
            if _model_is_cached(cfg.embed_model):
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                _prefer_offline_hub()

            from sentence_transformers import SentenceTransformer

            # 同步加载丢进线程池: SentenceTransformer() 是阻塞调用, 模型
            # 未缓存时 HEAD 重试可阻塞分钟级, 直接调用会冻住 uvicorn
            # 事件循环 → 全站不响应 (内网实调现象, /health 也打不进)
            st_model = await asyncio.to_thread(SentenceTransformer, cfg.embed_model)

            async def embedding_func(texts: list[str]):
                # encode 同为阻塞调用 (CPU 上 m3 批次级秒), 一并丢线程池
                return await asyncio.to_thread(
                    st_model.encode, texts, normalize_embeddings=True
                )

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
                # entity_types_guidance: 约束抽取类型, 抑制字段名/日期噪声
                # (WeKnora 同款思路: 固定实体类型 + 自定义指令)。
                addon_params={
                    "language": cfg.rag_language,
                    "entity_types_guidance": cfg.rag_entity_guidance,
                },
            )
            await self._rag.initialize_storages()
            self._QueryParam = QueryParam
            self._ready = True
            return True
        except Exception:
            self._ready = False
            return False

    async def enumerate_entities(self, project_path: str, hint: str) -> list[RagEntity]:
        """实体枚举 (Q1 全通道)。以 local 模式取结构化实体 JSON。

        Q1 要求完整性: top_k 提到 400 避免默认 40 的截断; 噪声在客户端
        再过滤一层 (提取引导之外的兜底)。
        """
        if not await self.ensure_ready(project_path):
            return []
        prompt = f"列出文档中所有与「{hint}」相关的实体（组件/接口/服务/概念），逐条输出 JSON 实体清单"
        try:
            result = await self._rag.aquery(
                prompt,
                param=self._QueryParam(
                    mode="local",
                    only_need_context=True,
                    enable_rerank=False,
                    top_k=400,
                ),
            )
        except Exception:
            return []
        entities: list[RagEntity] = []
        seen: set[str] = set()
        for m in _ENTITY_LINE_RE.finditer(str(result)):
            name, etype, desc = m.group(1), m.group(2), (m.group(3) or "")
            if not name or name in seen or _is_noise(name) or etype in _NOISE_TYPES:
                continue
            seen.add(name)
            entities.append(
                RagEntity(name=name, entity_type=etype, description=desc)
            )
        return entities

    async def query(self, project_path: str, text: str, mode: str = "hybrid") -> str:
        """机制型/链式查询 (Q2)。"""
        if not await self.ensure_ready(project_path):
            return ""
        try:
            result = await self._rag.aquery(
                text,
                param=self._QueryParam(
                    mode=mode, enable_rerank=False, include_references=True
                ),
            )
            return str(result)
        except Exception:
            return ""

    async def query_context(
        self, project_path: str, text: str, mode: str = "hybrid", top_k: int = 3
    ) -> list[RagChunk]:
        """检索上下文 (引用层): 纯检索无 LLM 生成, 取 chunk 原文作引用。"""
        if not await self.ensure_ready(project_path):
            return []
        try:
            raw = await self._rag.aquery(
                text,
                param=self._QueryParam(
                    mode=mode,
                    only_need_context=True,
                    enable_rerank=False,
                    top_k=top_k,
                ),
            )
        except Exception:
            return []
        return parse_context_chunks(str(raw))

    async def ingest(
        self, project_path: str, text: str, source_name: str = ""
    ) -> bool:
        """摄入。source_name 会写入 reference 列表 (引用层路径锚点)。"""
        if not await self.ensure_ready(project_path):
            return False
        try:
            await self._rag.ainsert(
                text, file_paths=[source_name] if source_name else None
            )
            return True
        except Exception:
            return False
