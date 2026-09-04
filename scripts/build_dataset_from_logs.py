#!/usr/bin/env python3
"""从查询日志沉淀评测黄金集 (内网部署后 2-3 周执行一次)。

query_logs 表自动记录所有真实查询 (P1b 起); 本脚本按频次去重后导出
待标注模板 (eval/pending_annotation.jsonl), 人工填 reference 后
合并进 eval/sample_dataset.jsonl 作为黄金集。

用法:
  CRUCIBLE_DATABASE_URL=... python scripts/build_dataset_from_logs.py \
      --top 50 --days 21 --out eval/pending_annotation.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone


def _norm(q: str) -> str:
    q = unicodedata.normalize("NFKC", q).lower()
    q = re.sub(r"\s+", " ", q).strip()
    return q


async def pull_questions(database_url: str, days: int) -> list[dict]:
    import asyncpg

    conn = await asyncpg.connect(database_url)
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await conn.fetch(
            """SELECT query, qtype, project_id, count(*) AS n
               FROM query_logs
               WHERE created_at >= $1 AND length(trim(query)) >= 4
               GROUP BY query, qtype, project_id
               ORDER BY n DESC""",
            cutoff,
        )
    finally:
        await conn.close()
    return [
        {"query": r["query"], "qtype": r["qtype"], "project_id": r["project_id"], "n": r["n"]}
        for r in rows
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--days", type=int, default=21)
    parser.add_argument("--out", default="eval/pending_annotation.jsonl")
    args = parser.parse_args()

    import os

    dsn = os.environ.get(
        "CRUCIBLE_DATABASE_URL",
        "postgresql+asyncpg://crucible:crucible@127.0.0.1:5432/crucible",
    ).replace("postgresql+asyncpg://", "postgresql://")

    rows = asyncio.run(pull_questions(dsn, args.days))
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in rows:
        key = _norm(r["query"])
        if key in seen or len(key) < 4:
            continue
        seen.add(key)
        deduped.append(r)
        if len(deduped) >= args.top:
            break

    qtype_dist = Counter(r["qtype"] or "?" for r in deduped)
    print(f"去重后 Top {len(deduped)} 条 (近 {args.days} 天):")
    for t, c in qtype_dist.most_common():
        print(f"  {t}: {c}")

    out_rows = [
        {
            "question": r["query"],
            "reference": "",          # ← 人工填写标准答案
            "project": r["project_id"],
            "qtype": r["qtype"],
            "source": f"query_logs×{r['n']}",
        }
        for r in deduped
    ]
    with open(args.out, "w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"待标注模板 → {args.out} (填 reference 后合并进 sample_dataset.jsonl)")


if __name__ == "__main__":
    main()
