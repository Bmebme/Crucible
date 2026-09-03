"""服务端配置: 统一 .env / 环境变量 (统一 LLM 约定见 docs/engineering-plan.md §3)。

优先级: 环境变量 > deploy/.env > 默认值。所有引擎共用一个 LLM。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("deploy/.env", ".env"),
        env_file_encoding="utf-8",
        env_prefix="CRUCIBLE_",
        extra="ignore",
    )

    # —— 统一 LLM (所有引擎同一个) ——
    llm_base: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    # —— 引擎 ——
    wiki_base: str = "http://127.0.0.1:19828"  # docker 内用 host.docker.internal
    rag_workdir_root: str = ""   # 空 = <project_path>/.lightrag
    embed_model: str = "BAAI/bge-small-zh-v1.5"
    embed_dim: int = 512
    rag_language: str = "Simplified Chinese"
    rag_entity_guidance: str = (
        "实体应为产品知识中的组件、接口、服务、模块、架构概念、领域、协议、部署形态等。"
        "不要提取日期、版本号、数值、文档结构字段名(如 name/type/description)、"
        "表格表头、以及 ENTITY_START 之类的模板占位符。"
    )
    alias_mode: str = "l2+l3"
    aliases_file: str = "kb-aliases.yaml"

    # —— 数据库 ——
    database_url: str = "postgresql+asyncpg://crucible:crucible@127.0.0.1:5432/crucible"

    # —— 服务 ——
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"


@lru_cache
def get_settings() -> Settings:
    return Settings()
