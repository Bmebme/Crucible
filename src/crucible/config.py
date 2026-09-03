"""融合层配置。

引擎地址与 LLM 凭据来自环境变量 (12-factor 风格), 带本地默认值:

  CRUCIBLE_WIKI_BASE     llm_wiki 后端地址        (默认 http://127.0.0.1:19828)
  CRUCIBLE_RAG_WORKDIR   LightRAG 工作目录       (默认 <project>/.lightrag)
  CRUCIBLE_LLM_BASE      OpenAI 兼容 LLM 端点     (默认 https://api.deepseek.com)
  CRUCIBLE_LLM_API_KEY   LLM API Key
  CRUCIBLE_LLM_MODEL     LLM 模型名               (默认 deepseek-chat)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    wiki_base: str = field(
        default_factory=lambda: os.environ.get(
            "CRUCIBLE_WIKI_BASE", "http://127.0.0.1:19828"
        ).rstrip("/")
    )
    rag_workdir: str | None = field(
        default_factory=lambda: os.environ.get("CRUCIBLE_RAG_WORKDIR") or None
    )
    llm_base: str = field(
        default_factory=lambda: os.environ.get(
            "CRUCIBLE_LLM_BASE", "https://api.deepseek.com"
        ).rstrip("/")
    )
    llm_api_key: str = field(
        default_factory=lambda: os.environ.get("CRUCIBLE_LLM_API_KEY", "")
    )
    llm_model: str = field(
        default_factory=lambda: os.environ.get("CRUCIBLE_LLM_MODEL", "deepseek-chat")
    )
    # 本地嵌入模型 (LightRAG demo 级适配用)
    embed_model: str = field(
        default_factory=lambda: os.environ.get(
            "CRUCIBLE_EMBED_MODEL", "BAAI/bge-small-zh-v1.5"
        )
    )
    embed_dim: int = 512
    # LightRAG 提取语言 (L1 跨语言对齐: 中文语料 → 中文实体名, §5.5)
    rag_language: str = field(
        default_factory=lambda: os.environ.get(
            "CRUCIBLE_RAG_LANGUAGE", "Simplified Chinese"
        )
    )
    # LightRAG 实体类型引导 (§5.5 附注: 约束抽取目标类型, 抑制字段名/日期/版本号噪声)
    rag_entity_guidance: str = field(
        default_factory=lambda: os.environ.get(
            "CRUCIBLE_RAG_ENTITY_GUIDANCE",
            "实体应为产品知识中的组件、接口、服务、模块、架构概念、领域、协议、部署形态等。"
            "不要提取日期、版本号、数值、文档结构字段名(如 name/type/description)、"
            "表格表头、以及 ENTITY_START 之类的模板占位符。",
        )
    )
    # 名字对齐模式 (§5.5 L2/L3, decisions.md D-08):
    #   l2+l3  词典先行, 未命中走 LLM 消解 (默认)
    #   l3     跳过词典, 全部走 LLM 消解
    #   off    关闭对齐, 纯字符串归一化比对 (基线)
    alias_mode: str = field(
        default_factory=lambda: os.environ.get("CRUCIBLE_ALIAS_MODE", "l2+l3")
    )
    # L2 别名词典文件名 (相对项目根)
    aliases_file: str = field(
        default_factory=lambda: os.environ.get("CRUCIBLE_ALIASES_FILE", "kb-aliases.yaml")
    )

    def rag_workdir_for(self, project_path: str) -> str:
        return self.rag_workdir or str(Path(project_path) / ".lightrag")


DEFAULT_CONFIG = Config()
