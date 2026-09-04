"""枚举结果格式化 + LLM 导读 (UI 与 MCP 共用)。"""
from __future__ import annotations

import httpx

GROUP_LABEL = {
    "wiki:concepts": "概念页", "wiki:entities": "实体页", "wiki:queries": "查询页",
    "wiki:sources": "源文档", "wiki:verification": "验证记录", "rag": "LightRAG 实体",
}


def group_enum_results(results: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in results:
        n = str(r.get("name", ""))
        if n.startswith("wiki/"):
            seg = n[5:].split("/")[0]
            groups.setdefault(f"wiki:{seg}", []).append(r)
        else:
            groups.setdefault("rag", []).append(r)
    for g in groups.values():
        g.sort(key=lambda r: r.get("name", ""))
    return groups


def format_enum_compact(data: dict, category: str = "", full: bool = False) -> str:
    """紧凑文本 (MCP 用)。"""
    groups = group_enum_results(data.get("results", []))
    diff_wiki = [d["item"] for d in data.get("differences", []) if d.get("only_in") == "wiki"]
    diff_rag = [d["item"] for d in data.get("differences", []) if d.get("only_in") == "rag"]
    alias_notes = [n for n in data.get("notes", []) if n.startswith("alias:")]

    names = {k: [r.get("name", "") for r in v] for k, v in groups.items()}
    lines: list[str] = []

    def annotate(name: str, max_len: int = 60) -> str:
        """条目 + 一句话简介 (wiki snippet / rag 实体描述)。"""
        for r in data.get("results", []):
            if r.get("name") != name:
                continue
            brief = (r.get("snippet") or r.get("description") or "").strip()
            brief = " ".join(brief.split())
            if brief:
                return f"{name} — {brief[:max_len]}"
            return name
        return name

    if category:
        key = f"wiki:{category}" if category != "rag" else "rag"
        items = names.get(key, [])
        cap = len(items) if full else 30
        shown, rest = items[:cap], items[cap:]
        lines.append(f"【{GROUP_LABEL.get(key, key)}】{len(items)} 项"
                     + ("（完整）" if not rest else f"（显示前 {cap}，余 {len(rest)} 项用 full=true 拉全）"))
        lines.extend(f"- {annotate(i)}" for i in shown)
    else:
        total = len(data.get("results", []))
        wiki_n = sum(len(v) for k, v in names.items() if k != "rag")
        rag_n = len(names.get("rag", []))
        lines.append(f"【枚举】共 {total} 项: wiki {wiki_n} 页 + rag {rag_n} 实体")
        for key in ("wiki:concepts", "wiki:entities", "wiki:verification", "wiki:sources", "wiki:queries", "rag"):
            if key not in names:
                continue
            items = names[key]
            cap = 10
            shown, rest = items[:cap], items[cap:]
            annotated = "; ".join(annotate(i) for i in shown)
            lines.append(f"- {GROUP_LABEL.get(key, key)} ({len(items)}): {annotated}"
                         + (f" …余 {len(rest)}" if rest else ""))
    if diff_wiki or diff_rag:
        lines.append(f"差异: wiki 侧缺 {len(diff_rag)} / rag 侧缺 {len(diff_wiki)} (知识缺口)")
    if alias_notes:
        lines.append(f"名字对齐: {len(alias_notes)} 条命中")
    return "\n".join(lines)


_SUMMARY_PROMPT = """你是漏洞验证知识库的导读助手。下面是「{hint}」的枚举清单分组总览。
写一段 3-6 句的中文导读, 回答「有哪些」:
1. 先点出最相关的核心条目 (优先实体页/验证记录/概念页, 跳过纯导航性页面)
2. 提一下总量与构成
3. 结尾注明: 完整清单以分组列表为准, 本导读为生成文本
只输出导读正文, 不要标题、不要列表。

{compact}
"""


async def summarize_enum(
    hint: str, data: dict, llm_base: str, llm_key: str, llm_model: str
) -> str:
    """LLM 导读 (失败返回空串, UI 降级为纯清单)。"""
    if not llm_key:
        return ""
    compact = format_enum_compact(data)
    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as c:
            resp = await c.post(
                f"{llm_base}/chat/completions",
                headers={"Authorization": f"Bearer {llm_key}"},
                json={
                    "model": llm_model,
                    "messages": [{"role": "user", "content": _SUMMARY_PROMPT.format(hint=hint, compact=compact)}],
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""
