#!/usr/bin/env python3
"""存量项目批量补摄入: 把老项目的 wiki md 过一遍 crucible 上传通道。

用法 (宿主机 WSL, 需可访问 crucible 8080):
  python3 scripts/batch_reingest.py <project_id> [crucible_base] [data_root]

行为 (冲突防护):
  - 只扫 <data_root>/<project_id>/wiki/**/*.md
  - 跳过 verification/ 子目录 (验证记录语义保留, 不重传)
  - 跳过 raw/sources/ 已存在的同名文件 (防 RAG 重复 chunk)
  - 逐个 POST /projects/{id}/documents (md 直传通道: 统一源 + RAG + 知识页)
  - 失败不中断, 末尾汇总
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

project_id = sys.argv[1]
crucible = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8080"
data_root = Path(sys.argv[3]) if len(sys.argv) > 3 else Path.home() / "kb-data"

wiki_root = data_root / project_id / "wiki"
sources_root = data_root / project_id / "raw" / "sources"
if not wiki_root.exists():
    print(f"未找到 wiki 目录: {wiki_root}")
    sys.exit(1)

files = [p for p in sorted(wiki_root.rglob("*.md")) if p.is_file()]
skipped_verif = [p for p in files if "verification" in p.parts]
files = [p for p in files if "verification" not in p.parts]
skipped_dup = [p for p in files if (sources_root / p.name).exists()]
files = [p for p in files if not (sources_root / p.name).exists()]

print(f"共 {len(files) + len(skipped_verif) + len(skipped_dup)} 个 md: "
      f"待传 {len(files)}, 跳过 verification {len(skipped_verif)}, "
      f"跳过已摄入 {len(skipped_dup)}")

ok = fail = 0
with httpx.Client(timeout=1800.0, trust_env=False) as c:
    for p in files:
        try:
            r = c.post(
                f"{crucible}/projects/{project_id}/documents",
                files={"file": (p.name, p.read_bytes(), "text/markdown")},
                data={"subdir": ""},
            )
            r.raise_for_status()
            j = r.json()
            if j.get("ok"):
                ok += 1
                print(f"  ✓ {p.relative_to(wiki_root)} → job {j.get('job_id')}")
            else:
                fail += 1
                print(f"  ✗ {p.relative_to(wiki_root)}: {j}")
        except Exception as e:
            fail += 1
            print(f"  ✗ {p.relative_to(wiki_root)}: {e}")

print(f"完成: 成功 {ok}, 失败 {fail}")
print("注意: 任务异步执行, 前端「摄入任务」页可看每个文件的阶段进度。")
