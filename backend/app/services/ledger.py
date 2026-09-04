"""台账与审核队列持久化 (P5)。"""
from __future__ import annotations

from pathlib import Path

import yaml
from sqlalchemy import select

from ..db import session_scope
from ..models import AliasReview, ConflictLedger, DiffLedger

_ALIAS_PAIR_PREFIX = "alias_llm: "


def _parse_alias_pairs(notes: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for n in notes:
        # orchestrator 的 notes 形如 "alias: alias_llm: A ↔ B"
        body = n[len("alias: "):] if n.startswith("alias: ") else n
        if not body.startswith(_ALIAS_PAIR_PREFIX):
            continue
        body = body[len(_ALIAS_PAIR_PREFIX):]
        if " ↔ " in body:
            a, b = body.split(" ↔ ", 1)
            pairs.append((a.strip(), b.strip()))
    return pairs


async def save_enum_artifacts(
    project_id: str, query: str, differences: list[dict], notes: list[str]
) -> None:
    """枚举落账: 差异清单 + L3 判定对进审核队列 (去重: 同项 open 不再插)。"""
    async with session_scope() as s:
        for d in differences:
            item, only_in = str(d.get("item", "")), str(d.get("only_in", ""))
            if not item or only_in not in ("wiki", "rag"):
                continue
            exists = await s.scalar(
                select(DiffLedger.id).where(
                    DiffLedger.project_id == project_id,
                    DiffLedger.item == item,
                    DiffLedger.only_in == only_in,
                    DiffLedger.status == "open",
                )
            )
            if exists is None:
                s.add(DiffLedger(
                    project_id=project_id, query=query, item=item,
                    only_in=only_in, action=str(d.get("action", "")),
                ))
        for a, b in _parse_alias_pairs(notes):
            exists = await s.scalar(
                select(AliasReview.id).where(
                    AliasReview.project_id == project_id,
                    AliasReview.name_a == a, AliasReview.name_b == b,
                    AliasReview.status.in_(["pending", "approved"]),
                )
            )
            if exists is None:
                s.add(AliasReview(project_id=project_id, name_a=a, name_b=b))


async def save_conflicts(project_id: str, query: str, conflicts: list[dict]) -> None:
    async with session_scope() as s:
        for c in conflicts:
            s.add(ConflictLedger(
                project_id=project_id, query=query,
                wiki_says=c.get("wiki_says") or {},
                rag_says=c.get("rag_says") or {},
            ))


async def list_diffs(project_id: str, limit: int = 100) -> list[dict]:
    async with session_scope() as s:
        rows = (await s.scalars(
            select(DiffLedger)
            .where(DiffLedger.project_id == project_id, DiffLedger.status == "open")
            .order_by(DiffLedger.id.desc()).limit(limit)
        )).all()
    return [
        {"id": r.id, "item": r.item, "only_in": r.only_in, "action": r.action,
         "status": r.status, "created_at": r.created_at.isoformat() if r.created_at else ""}
        for r in rows
    ]


async def list_conflicts(project_id: str, limit: int = 50) -> list[dict]:
    async with session_scope() as s:
        rows = (await s.scalars(
            select(ConflictLedger)
            .where(ConflictLedger.project_id == project_id, ConflictLedger.status == "open")
            .order_by(ConflictLedger.id.desc()).limit(limit)
        )).all()
    return [
        {"id": r.id, "query": r.query, "wiki_says": r.wiki_says,
         "rag_says": r.rag_says, "resolution": r.resolution,
         "created_at": r.created_at.isoformat() if r.created_at else ""}
        for r in rows
    ]


async def list_alias_reviews(project_id: str, status: str = "pending") -> list[dict]:
    async with session_scope() as s:
        rows = (await s.scalars(
            select(AliasReview)
            .where(AliasReview.project_id == project_id, AliasReview.status == status)
            .order_by(AliasReview.id.desc())
        )).all()
    return [
        {"id": r.id, "name_a": r.name_a, "name_b": r.name_b, "status": r.status,
         "created_at": r.created_at.isoformat() if r.created_at else ""}
        for r in rows
    ]


async def resolve_alias_review(
    project_id: str, review_id: int, action: str, project_path: str, aliases_file: str
) -> dict:
    async with session_scope() as s:
        row = await s.get(AliasReview, review_id)
        if row is None or row.project_id != project_id:
            return {"ok": False, "error": "记录不存在"}
        if row.status != "pending":
            return {"ok": False, "error": f"已处理 ({row.status})"}
        row.status = "approved" if action == "approve" else "rejected"
        pair = (row.name_a, row.name_b)
        approved = action == "approve"

    if approved:
        err = _append_alias_group(Path(project_path) / aliases_file, pair)
        if err:
            return {"ok": False, "error": f"词典回写失败: {err}"}
    return {"ok": True, "status": "approved" if approved else "rejected", "pair": list(pair)}


def _append_alias_group(aliases_path: Path, pair: tuple[str, str]) -> str | None:
    """把等价对写进 kb-aliases.yaml 的 groups (幂等)。失败返回错误串。"""
    try:
        data = yaml.safe_load(aliases_path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            data = {}
        groups = data.get("groups") or []
        if not isinstance(groups, list):
            groups = []
        pair_list = list(pair)
        if pair_list not in groups and list(reversed(pair_list)) not in groups:
            groups.append(pair_list)
        data["groups"] = groups
        # 保留 splits 等其余字段
        aliases_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return None
    except (OSError, yaml.YAMLError) as e:
        return str(e)


async def resolve_diff(
    project_id: str, diff_id: int, action: str, note: str = ""
) -> dict:
    async with session_scope() as s:
        row = await s.get(DiffLedger, diff_id)
        if row is None or row.project_id != project_id:
            return {"ok": False, "error": "记录不存在"}
        row.status = "resolved" if action == "resolve" else row.status
        return {"ok": True, "id": diff_id, "status": row.status}


async def resolve_conflict(
    project_id: str, conflict_id: int, resolution: str, note: str = ""
) -> dict:
    async with session_scope() as s:
        row = await s.get(ConflictLedger, conflict_id)
        if row is None or row.project_id != project_id:
            return {"ok": False, "error": "记录不存在"}
        row.resolution = resolution
        row.note = note
        row.status = "resolved"
        return {"ok": True, "id": conflict_id, "resolution": resolution}
