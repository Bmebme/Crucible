"""llm_wiki 页面引擎适配。

封装后端 HTTP API (19828 契约):
  - search: 混合检索 (token + 向量候选 + 一跳图)
  - list_pages: wiki/ 页面清单 (枚举通道)
  - read_page: 页面内容 + frontmatter (M3 取 verify_state)
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from ..schemas import Citation, WikiHit

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# wiki 后端是 localhost 服务: 禁止 httpx 读取环境代理 (trust_env),
# 否则 HTTP_PROXY 等环境变量会把本机流量劫持到代理导致超时。
_CLIENT_KW = {"timeout": 30.0, "trust_env": False}


class WikiEngine:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def search(self, project_id: str, query: str, limit: int = 8) -> list[WikiHit]:
        async with httpx.AsyncClient(**_CLIENT_KW) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/projects/{project_id}/search",
                json={"query": query, "limit": limit},
            )
            data = resp.json()
        if not data.get("ok"):
            return []
        hits: list[WikiHit] = []
        for r in data.get("results") or []:
            path = r.get("path", "")
            snippet = (r.get("snippet") or "")[:200]
            hits.append(
                WikiHit(
                    title=r.get("title") or path,
                    path=path,
                    score=float(r.get("score") or 0),
                    snippet=snippet,
                    source=r.get("source", ""),
                    # 引用层: wiki 页面即原文, path 即指针 (excerpt=snippet 定位)
                    citations=[Citation(source="wiki", path=path, excerpt=snippet)]
                    if path
                    else [],
                )
            )
        return hits

    async def read_page_content(self, project_id: str, path: str) -> str:
        """整页原文 (引用层: 跳转原文用)。失败返回空串。"""
        try:
            async with httpx.AsyncClient(**_CLIENT_KW) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/projects/{project_id}/files/content",
                    params={"path": path},
                )
                if resp.status_code != 200:
                    return ""
                data = resp.json()
        except Exception:
            return ""
        content = data.get("content", "")
        return content if isinstance(content, str) else ""

    async def list_pages(self, project_id: str) -> list[str]:
        """wiki/ 下全部页面相对路径 (不含 .md)。

        注意: files API 返回的是树结构 (isDir + children), 需递归展开;
        index/log/overview 是导航页, 不属于枚举实体, 过滤掉。
        """
        async with httpx.AsyncClient(**_CLIENT_KW) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/projects/{project_id}/files",
                params={"root": "wiki", "recursive": "true", "maxFiles": "500"},
            )
            data = resp.json()
        if not data.get("ok"):
            return []

        paths: list[str] = []

        def walk(items: list[Any]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                path = item.get("path", "")
                if item.get("isDir"):
                    walk(item.get("children") or [])
                    continue
                if isinstance(path, str) and path.endswith(".md"):
                    rel = path[: -len(".md")]
                    if rel in ("wiki/index", "wiki/log", "wiki/overview"):
                        continue
                    paths.append(rel)

        walk(data.get("files") or [])
        return paths

    async def read_page_frontmatter(self, project_id: str, path: str) -> dict[str, Any]:
        """读取页面 frontmatter (M3 verify_state 用)。失败返回空。"""
        async with httpx.AsyncClient(**_CLIENT_KW) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/projects/{project_id}/files/content",
                params={"path": path},
            )
            if resp.status_code != 200:
                return {}
            try:
                data = resp.json()
            except Exception:
                return {}
        content = data.get("content", "")
        if isinstance(content, (dict, list)):
            return {}
        m = _FRONTMATTER_RE.match(content or "")
        if not m:
            return {}
        fm: dict[str, Any] = {}
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip("\"'")
        return fm
