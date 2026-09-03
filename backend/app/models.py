"""元数据层模型 (docs/engineering-plan.md §4): PG 只存管理性数据, 不存知识。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)  # 项目根目录
    wiki_project_id: Mapped[str] = mapped_column(
        String(64), default=""
    )  # llm-wiki 侧的项目 id (可与本系统 id 不同)
    rag_workdir: Mapped[str] = mapped_column(
        Text, default=""
    )  # LightRAG 工作目录覆盖 (空 = <path>/.lightrag)
    wiki_base: Mapped[str] = mapped_column(String(256), default="")
    alias_mode: Mapped[str] = mapped_column(String(16), default="l2+l3")
    rag_language: Mapped[str] = mapped_column(String(64), default="Simplified Chinese")
    rag_entity_guidance: Mapped[str] = mapped_column(Text, default="")
    aliases_file: Mapped[str] = mapped_column(String(128), default="kb-aliases.yaml")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(16), default="md")  # md/txt/...
    status: Mapped[str] = mapped_column(
        String(16), default="uploaded"
    )  # uploaded→wiki_indexed→rag_ingested→done / failed
    wiki_path: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class QueryLog(Base):
    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    query: Mapped[str] = mapped_column(Text)
    qtype: Mapped[str] = mapped_column(String(8), default="")
    alias_mode: Mapped[str] = mapped_column(String(16), default="")
    rewritten_to: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    result_kind: Mapped[str] = mapped_column(String(16), default="")  # enum/mechanism/experience
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
