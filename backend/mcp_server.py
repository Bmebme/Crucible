"""Crucible MCP Server (P6): 给漏洞验证 Agent 的融合知识工具集。

工具描述带检索阶梯提示 (WeKnora 模式, engineering-plan §6):
场景/目标枚举先行 → 机制查询 → 经验验证 → 结果回写。

运行: CRUCIBLE_API_BASE=http://127.0.0.1:8080 python mcp_server.py
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API = os.environ.get("CRUCIBLE_API_BASE", "http://127.0.0.1:8080")

mcp = FastMCP(
    "crucible-kb",
    instructions=(
        "漏洞验证知识库工具集。使用阶梯: ① 场景/目标枚举 (kb_enum) 明确攻击面 → "
        "② 机制查询 (kb_mechanism) 搞清实现细节 → ③ 经验查询 (kb_experience) 查历史"
        "验证记录 → ④ 验证完成后 kb_record_verification 回写 (形成经验闭环)。"
        "所有结果带原文引用 (citations), 规划攻击路径前先看引用原文。"
    ),
)


async def _post(path: str, body: dict[str, Any]) -> dict:
    async with httpx.AsyncClient(timeout=600.0, trust_env=False) as c:
        resp = await c.post(f"{API}{path}", json=body)
        resp.raise_for_status()
        return resp.json()


async def _upload_verification(
    project_id: str, title: str, verify_state: str, env: str, content: str
) -> dict:
    md = (
        f"---\nverify_state: {verify_state}\n"
        + (f"verify_env: {env}\n" if env else "")
        + "---\n\n"
        + f"# {title}\n\n{content}\n"
    )
    async with httpx.AsyncClient(timeout=600.0, trust_env=False) as c:
        resp = await c.post(
            f"{API}/projects/{project_id}/documents",
            data={"subdir": "verification"},
            files={"file": (f"{title}.md", md.encode("utf-8"))},
        )
        resp.raise_for_status()
        return resp.json()


def _format_enum(data: dict, category: str = "", full: bool = False) -> str:
    import sys as _sys

    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app.services.formatting import format_enum_compact

    return format_enum_compact(data, category=category, full=full)


def _format_experience(data: dict) -> str:
    lines = [f"【验证记录】{len(data.get('results', []))} 条 (按 verify_state 加权降序)"]
    for r in data.get("results", [])[:15]:
        lines.append(
            f"- [{r.get('weight')}] {r.get('state')}: {r.get('title') or r.get('path')}"
            + (f" — {r.get('note')}" if r.get("note") else "")
        )
    if len(data.get("results", [])) > 15:
        lines.append(f"…余 {len(data['results']) - 15} 条")
    return "\n".join(lines)


@mcp.tool()
async def kb_enum(
    hint: str,
    project_id: str = "mae",
    category: str = "",
    full: bool = False,
    related: bool = False,
) -> str:
    """枚举项目内与某主题相关的组件/接口/服务/概念 (Q1, 指标: 召回率)。

    用途: 攻击场景构建阶段 —— 先摸清攻击面。步骤①, 在 kb_mechanism 之前调用。
    hint 用具体名词, 如「组件」「外部接口」「文件处理」「鉴权」。

    返回紧凑文本清单 (统计 + 分组 + 截断), 不会一次性倾倒全部条目;
    需要某类完整条目时用 category (concepts/entities/queries/sources/
    verification/rag) + full=true 逐类拉取。
    """
    data = await _post("/fusion/enum", {"hint": hint, "project_id": project_id, "include_related": related})
    out = _format_enum(data, category=category, full=full)
    if related and data.get("related_hits"):
        lines = [f"【关联产品参考 (权重 0.1)】"]
        for rh in data["related_hits"]:
            names = "; ".join(str(r.get("name", "")) for r in rh.get("results", [])[:8])
            lines.append(f"- {rh['project']}: {names}")
        out += "\n" + "\n".join(lines)
    return out


@mcp.tool()
async def kb_mechanism(
    query: str, project_id: str = "mae", history: list[str] | None = None
) -> dict:
    """查询产品内部机制的精确事实 (Q2 机制型, 指标: 精确率)。

    用途: 攻击路径规划阶段 —— 数据流/调用链/鉴权实现等「怎么做的」问题。
    步骤②, 在 kb_enum 之后; 追问时可传 history (指代消解)。
    结果带文段级引用 (citations), 做决策前必读引用原文; 冲突时不裁决, 由你判断。
    """
    return await _post(
        "/fusion/query",
        {"query": query, "project_id": project_id, "history": history or []},
    )


@mcp.tool()
async def kb_experience(query: str, project_id: str = "mae", env: str = "staging") -> str:
    """查询历史验证记录/拦截特征/误报记录 (Q3 经验型, 指标: 可信度)。

    用途: POC 生成与验证阶段 —— 「以前打过什么/被什么拦过/成功过吗」。
    步骤③; 结果按 verify_state 加权排序 (成功>未验证>拦截负知识),
    blocked 记录仅在环境匹配时返回。返回紧凑文本清单 (最多 15 条)。
    """
    data = await _post(
        "/fusion/experience", {"query": query, "project_id": project_id, "env": env},
    )
    return _format_experience(data)


@mcp.tool()
async def kb_record_verification(
    title: str,
    verify_state: str,
    content: str,
    project_id: str = "mae",
    env: str = "staging",
) -> dict:
    """回写一次实际验证结果到知识库 (步骤④, 经验闭环)。

    verify_state 四选一: verified_success (验证成功) / verified_blocked (被拦截,
    content 里写拦截特征) / unverified (未实测) / false_positive (误报)。
    回写后同一环境下的 kb_experience 查询即可检索到, 按状态加权排序。
    """
    if verify_state not in (
        "verified_success", "verified_blocked", "unverified", "false_positive",
    ):
        return {"ok": False, "error": "verify_state 非法, 须为四态之一"}
    return await _upload_verification(project_id, title, verify_state, env, content)


if __name__ == "__main__":
    mcp.run(transport="stdio")
