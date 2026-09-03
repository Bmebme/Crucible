"""融合层 CLI。

用法:
  crucible query "MAE 有哪些组件" --project-id current --project-path <路径>
  crucible enum 组件 --project-path <路径>
  crucible experience "上传验证" --project-path <路径> --env staging
"""
from __future__ import annotations

import argparse
import asyncio
import json

from .config import Config
from .orchestrator import FusionOrchestrator


def _print_response(resp) -> None:
    print(json.dumps(resp.to_dict(), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="crucible", description="Crucible 融合检索层")
    sub = parser.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("query", help="融合查询 (判别 → 双引擎 → 合并)")
    q.add_argument("text")
    q.add_argument(
        "--history",
        action="append",
        default=None,
        help="多轮追问的历史用户提问 (可重复, 最早在前), 用于指代消解",
    )
    _common(q)

    e = sub.add_parser("enum", help="Q1 枚举查询 (M1 并集合并)")
    e.add_argument("hint")
    _common(e)

    x = sub.add_parser("experience", help="Q3 经验查询 (M3 状态排序)")
    x.add_argument("text")
    x.add_argument("--env", default="")
    x.add_argument(
        "--history",
        action="append",
        default=None,
        help="多轮追问的历史用户提问 (可重复, 最早在前), 用于指代消解",
    )
    _common(x)

    args = parser.parse_args()

    config = Config()
    project_id = getattr(args, "project_id", "current")
    project_path = getattr(args, "project_path", "")
    orch = FusionOrchestrator(config, project_id=project_id, project_path=project_path)

    async def run() -> None:
        if args.cmd == "query":
            resp = await orch.run(
                args.text, env=getattr(args, "env", ""), history=args.history
            )
        elif args.cmd == "enum":
            # 枚举查询: 走 Q1 通道
            from .classifier import classify
            from .schemas import FusionResponse, QueryType
            from .merge.m1_union import union_merge

            import asyncio as _aio

            pages, entities = await _aio.gather(
                orch.wiki.list_pages(project_id),
                orch.rag.enumerate_entities(project_path, args.hint),
            )
            merged = union_merge(pages, [e.name for e in entities])
            resp = FusionResponse(query=f"enum:{args.hint}", routing=await classify(f"有哪些{args.hint}", config))
            resp.results = [{"kind": "item", "name": n, "provenance": ["union"]} for n in merged.union]
            resp.differences = merged.differences
            resp.notes.append(
                f"union={len(merged.union)} differences={len(merged.differences)}"
            )
        else:
            resp = await orch.run(args.text, env=args.env, history=args.history)
        _print_response(resp)

    asyncio.run(run())


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--project-id", default="current")
    p.add_argument("--project-path", default="")


if __name__ == "__main__":
    main()
