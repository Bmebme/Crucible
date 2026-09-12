"""Crucible MCP Server (P6): 给漏洞验证 Agent 的融合知识工具集。

主入口 kb_query (通用融合查询, 与平台查询台同一链路, 自动判别 Q1/Q2/Q3),
定向快捷 kb_enum / kb_experience, 回写 kb_record_verification (经验闭环),
复盘 kb_query_history。**所有工具返回结构化 JSON** (与融合服务接口同构,
供 Agent 程序化消费; 需要人类可读清单时在 Agent 侧自行渲染)。

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
        "漏洞验证知识库工具集 (全部返回结构化 JSON)。**所有工具 project_id 必填** "
        "(区分产品, 不填直接报错)。"
        "【首选】kb_query —— 通用融合查询, 与平台查询台同一链路, 自动判别 "
        "Q1 枚举/Q2 机制/Q3 经验; 任何「了解组件/机制/历史/清单」的问题都**优先用它**, "
        "能力最全、效果最好。"
        "kb_enum / kb_experience 是定向快捷 (仅在明确只要清单或只要历史记录时使用), "
        "覆盖面与效果弱于 kb_query。"
        "验证完成后 kb_record_verification 回写 (经验闭环); 复盘用 kb_query_history。"
        "所有检索结果带原文引用 (citations), 规划攻击路径前先看引用原文; "
        "两库冲突不裁决, 由你判断。"
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


@mcp.tool()
async def kb_query(
    query: str,
    project_id: str,
    history: list[str] | None = None,
    env: str = "",
) -> dict:
    """【首选工具】通用融合查询 —— 最高优先级入口, 与平台查询台同一链路 (自动判别类型)。

    自动判别 Q1 枚举 / Q2 机制 / Q3 经验并走对应合并 (M1/M2/M3)。返回完整
    响应 JSON: routing (判别) / results (结论 + llm-wiki chat 结论 + 双引擎
    原文证据块) / differences / conflicts / notes / timings / timed_out。

    任何「了解某组件/机制/历史」的问题都可直接用; 追问可传 history
    (指代消解)。结果带文段级引用 (citations), 决策前必读引用原文。
    """
    return await _post(
        "/fusion/query",
        {"query": query, "project_id": project_id,
         "history": history or [], "env": env},
    )


@mcp.tool()
async def kb_enum(hint: str, project_id: str, related: bool = False) -> dict:
    """枚举组件/接口/服务/概念清单 (定向快捷 —— 优先用 kb_query)。

    仅当明确只需要「攻击面清单」时使用; 若还需机制说明或结论, 直接用
    kb_query (问「有哪些…」即自动走枚举路径)。hint 用具体名词, 如
    「组件」「外部接口」「文件处理」「鉴权」; related=true 附关联产品
    低权重参考区。

    返回 JSON: results (并集清单, 含简介/实体类型) / differences (两库
    差异 = 知识缺口信号) / notes。
    """
    return await _post(
        "/fusion/enum",
        {"hint": hint, "project_id": project_id, "include_related": related},
    )


@mcp.tool()
async def kb_experience(query: str, project_id: str, env: str = "staging") -> dict:
    """查询历史验证记录/拦截特征/误报记录 (定向快捷 —— 优先用 kb_query)。

    仅当明确只要「按验证状态加权的记录清单」时使用。用途: POC 生成与
    验证阶段 —— 「以前打过什么/被什么拦过/成功过吗」。

    返回 JSON: results 按 verify_state 加权降序 (成功 1.0 > 未验证 0.5 >
    拦截负知识 0.2); blocked 记录仅在 env 匹配时返回 (env 传当前验证环境)。
    """
    return await _post(
        "/fusion/experience",
        {"query": query, "project_id": project_id, "env": env},
    )


@mcp.tool()
async def kb_record_verification(
    title: str,
    verify_state: str,
    content: str,
    project_id: str,
    env: str = "staging",
) -> dict:
    """回写一次实际验证结果到知识库 (经验闭环)。

    verify_state 四选一: verified_success (验证成功) / verified_blocked (被拦截,
    content 里写拦截特征) / unverified (未实测) / false_positive (误报)。
    回写后同一环境下的 kb_experience 查询即可检索到, 按状态加权排序。

    返回 JSON: {ok, job_id, message} —— 异步入库, 轮询
    GET /projects/{id}/documents 看阶段。
    """
    if verify_state not in (
        "verified_success", "verified_blocked", "unverified", "false_positive",
    ):
        return {"ok": False, "error": "verify_state 非法, 须为四态之一"}
    return await _upload_verification(project_id, title, verify_state, env, content)


@mcp.tool()
async def kb_query_history(project_id: str, limit: int = 5) -> dict:
    """读取项目最近的历史查询完整结果 (复盘/调优用)。

    返回 JSON: items[] 每条 = 一次查询的完整融合响应 (query/ts/routing/
    results/notes/timings), 供程序化分析弱模型输出质量与检索问题。
    """
    return await _post(
        "/fusion/query-history", {"project_id": project_id, "limit": limit}
    )


# Streamable HTTP 传输: 挂进 crucible 服务 (/mcp), 供多人远程调用
# (uvicorn 直接跑也可以: uvicorn backend.mcp_server:http_app)
http_app = mcp.streamable_http_app()


if __name__ == "__main__":
    mcp.run(transport="stdio")
