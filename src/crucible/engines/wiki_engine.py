"""llm_wiki 页面引擎适配。

封装后端 HTTP API (19828 契约):
  - search: 混合检索 (token + 向量候选 + 一跳图)
  - list_pages: wiki/ 页面清单 (枚举通道)
  - read_page: 页面内容 + frontmatter (M3 取 verify_state)
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

from ..schemas import Citation, WikiHit

logger = logging.getLogger("crucible.wiki")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def find_heading(content: str, snippet: str) -> str:
    """在整页原文里定位 snippet 所属的最近标题 (引用段落级锚点, 任务 8)。"""
    if not content or not snippet:
        return ""
    probe = " ".join(snippet.split())[:60]
    idx = content.find(probe)
    if idx < 0:
        return ""
    matches = list(_HEADING_RE.finditer(content[:idx]))
    return matches[-1].group(2).strip() if matches else ""

# wiki 后端是 localhost 服务: 禁止 httpx 读取环境代理 (trust_env),
# 否则 HTTP_PROXY 等环境变量会把本机流量劫持到代理导致超时。
_CLIENT_KW = {"timeout": 30.0, "trust_env": False}


class WikiEngine:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def search(self, project_id: str, query: str, limit: int = 8) -> list[WikiHit]:
        t0 = time.monotonic()
        async with httpx.AsyncClient(**_CLIENT_KW) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/projects/{project_id}/search",
                json={"query": query, "limit": limit},
            )
            data = resp.json()
        if not data.get("ok"):
            logger.warning(
                "wiki search FAILED: %s '%s' HTTP %s (%.2fs)",
                project_id, query[:60], resp.status_code, time.monotonic() - t0,
            )
            return []
        n = len(data.get("results") or [])
        logger.info(
            "wiki search: %s '%s' -> %d hits (%.2fs)",
            project_id, query[:60], n, time.monotonic() - t0,
        )
        hits: list[WikiHit] = []
        for r in data.get("results") or []:
            path = r.get("path", "")
            # 剥 frontmatter (evidence claim 与简介不再带 --- 元数据) 后取片段
            snippet = _FRONTMATTER_RE.sub("", r.get("snippet") or "").strip()[:500]
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

    async def chat_answer(self, project_id: str, query: str, mode: str = "deep") -> str:
        """调 llm-wiki chat 拿完整参考回答 (非流式聚合)。

        融合层的第三个参考输入 (内网实调: chat 的完整回答质量好,
        作为叙述底稿进 M2, 事实仍以双引擎证据为准)。失败抛异常,
        由上层降级为空。
        """
        t0 = time.monotonic()
        logger.info("wiki chat 开始: %s '%s' (内部: 检索+LLM 生成, 慢模型分钟级)",
                    project_id, query[:60])
        # 上限 240s: chat 参考是可降级项, 不该无限拖住查询 (内网实调)
        async with httpx.AsyncClient(timeout=240.0, trust_env=False) as client:
            resp = await client.post(
                f"{self.base_url}/api/v1/projects/{project_id}/chat",
                json={"message": query, "mode": mode, "topK": 8},
            )
            resp.raise_for_status()
            data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"chat ok=false: {str(data)[:200]}")
        msg = data.get("message") or {}
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        logger.info(
            "wiki chat: %s '%s' -> %d chars (%.2fs)",
            project_id, query[:60], len(content), time.monotonic() - t0,
        )
        return content if isinstance(content, str) else ""

    async def read_page_content(self, project_id: str, path: str) -> str:
        """整页原文 (引用层: 跳转原文用)。失败返回空串。"""
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(**_CLIENT_KW) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/projects/{project_id}/files/content",
                    params={"path": path},
                )
                if resp.status_code != 200:
                    logger.warning(
                        "wiki page read FAILED: %s %s HTTP %s (%.2fs)",
                        project_id, path, resp.status_code, time.monotonic() - t0,
                    )
                    return ""
                data = resp.json()
        except Exception as e:
            logger.warning("wiki page read FAILED: %s %s (%s)", project_id, path, e)
            return ""
        content = data.get("content", "")
        logger.info("wiki page read: %s %s (%d chars, %.2fs)",
                    project_id, path, len(content), time.monotonic() - t0)
        if isinstance(content, str):
            # 字节级清洗 (无效 UTF-8/控制字符, 与 orchestrator._sanitize 同源)
            content = content.encode("utf-8", errors="ignore").decode("utf-8")
            content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", content)
        return content if isinstance(content, str) else ""

    async def list_pages(self, project_id: str) -> list[str]:
        """wiki/ 下全部页面相对路径 (不含 .md)。

        注意: files API 返回的是树结构 (isDir + children), 需递归展开;
        index/log/overview 是导航页, 不属于枚举实体, 过滤掉。
        """
        t0 = time.monotonic()
        async with httpx.AsyncClient(**_CLIENT_KW) as client:
            resp = await client.get(
                f"{self.base_url}/api/v1/projects/{project_id}/files",
                params={"root": "wiki", "recursive": "true", "maxFiles": "500"},
            )
            data = resp.json()
        if not data.get("ok"):
            logger.warning("wiki list_pages FAILED: %s HTTP %s (%.2fs)",
                           project_id, resp.status_code, time.monotonic() - t0)
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
        logger.info("wiki list_pages: %s -> %d pages (%.2fs)",
                    project_id, len(paths), time.monotonic() - t0)
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
