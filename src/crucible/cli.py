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
    if getattr(args, "alias_mode", None):
        config.alias_mode = args.alias_mode
    project_id = getattr(args, "project_id", "current")
    project_path = getattr(args, "project_path", "")
    orch = FusionOrchestrator(config, project_id=project_id, project_path=project_path)

    async def run() -> None:
        if args.cmd == "query":
            resp = await orch.run(
                args.text, env=getattr(args, "env", ""), history=args.history
            )
        elif args.cmd == "enum":
            # 枚举查询: 走 orchestrator 的 Q1 通道 (含 L2/L3 名字对齐)
            resp = await orch.run_enum(args.hint)
        else:
            resp = await orch.run(args.text, env=args.env, history=args.history)
        _print_response(resp)

    asyncio.run(run())


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--project-id", default="current")
    p.add_argument("--project-path", default="")
    p.add_argument(
        "--alias-mode",
        default=None,
        choices=["l2+l3", "l3", "off"],
        help="名字对齐模式 (默认取 CRUCIBLE_ALIAS_MODE, 再默认 l2+l3)",
    )


if __name__ == "__main__":
    main()
