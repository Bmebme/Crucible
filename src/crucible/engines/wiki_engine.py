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

from ..schemas import WikiHit

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class WikiEngine:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def search(self, project_id: str, query: str, limit: int = 8) -> list[WikiHit]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/projects/{project_id}/search",
                json={"query": query, "limit": limit},
            )
            data = resp.json()
        if not data.get("ok"):
            return []
        hits: list[WikiHit] = []
        for r in data.get("results") or []:
            hits.append(
                WikiHit(
                    title=r.get("title") or r.get("path", ""),
                    path=r.get("path", ""),
                    score=float(r.get("score") or 0),
                    snippet=(r.get("snippet") or "")[:200],
                    source=r.get("source", ""),
                )
            )
        return hits

    async def list_pages(self, project_id: str) -> list[str]:
        """wiki/ 下全部页面相对路径 (不含 .md)。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/projects/{project_id}/files",
                params={"root": "wiki", "recursive": "true", "maxFiles": "500"},
            )
            data = resp.json()
        if not data.get("ok"):
            return []
        paths: list[str] = []
        for item in data.get("files") or []:
            path = item.get("path") if isinstance(item, dict) else item
            if isinstance(path, str) and path.endswith(".md"):
                paths.append(path[: -len(".md")])
        return paths

    async def read_page_frontmatter(self, project_id: str, path: str) -> dict[str, Any]:
        """读取页面 frontmatter (M3 verify_state 用)。失败返回空。"""
        async with httpx.AsyncClient(timeout=30.0) as client:
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
