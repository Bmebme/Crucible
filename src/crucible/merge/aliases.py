"""名字对齐 L2/L3 (设计文档 §5.5, decisions.md D-05/D-08)。

L2 别名词典 (kb-aliases.yaml, 人工确认的等价关系, 零成本):
  groups: 等价名组, 组内名字两两等价
  splits: 复合名 ↔ 原子名集合 (wiki 复合页吸收 rag 原子实体)

L3 LLM 消解 (词典未命中的候选对, 一次批量调用判断, 低置信不采纳):
  候选对先经字符串启发式剪枝 (前缀/后缀/子串), 避免 63×131 全交叉。

回写闭环: L3 判定为等价且高置信的结果由人工确认后写入 L2 词典。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import yaml

from ..config import Config
from .m1_union import normalize_name

_L3_BATCH_MAX = 40          # 单次 LLM 调用最多判定的名字对数
_L3_CONFIDENCE_MIN = 0.7    # 低于此置信不采纳, 保持差异呈现
_L3_PAIR_RE = re.compile(
    r'"same"\s*:\s*(true|false)[^}]*?"confidence"\s*:\s*([\d.]+)'
)

_STOP_TOKENS = {"的", "与", "和", "及", "or", "and", "of", "the", "for", "to", "in"}
_TOKEN_MIN_LEN = 3          # token 子串规则的最小长度 ("er"/"ir" 太短, 全域误命中)


class AliasDict:
    """L2 词典。groups/splits 均在加载时归一化 (与 M1 同规则)。"""

    def __init__(self, groups: list[list[str]] | None, splits: dict[str, list[str]] | None):
        self._group_of: dict[str, set[str]] = {}
        for group in groups or []:
            norms = {normalize_name(g) for g in group}
            for n in norms:
                self._group_of[n] = norms
        self._splits: dict[str, set[str]] = {
            normalize_name(k): {normalize_name(v) for v in vs}
            for k, vs in (splits or {}).items()
        }

    def equivalences(self, name: str) -> set[str]:
        """与 name 等价的全部归一化名 (含自身)。"""
        return set(self._group_of.get(normalize_name(name), {normalize_name(name)}))

    def split_atoms(self, name: str) -> set[str]:
        """name 若是复合名, 返回其原子名集合; 否则空集。"""
        return set(self._splits.get(normalize_name(name), set()))

    @property
    def is_empty(self) -> bool:
        return not self._group_of and not self._splits


def load_alias_dict(path: str | Path) -> AliasDict | None:
    """读 kb-aliases.yaml。文件不存在或解析失败返回 None (对齐静默关闭)。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    return AliasDict(
        groups=data.get("groups"),
        splits=data.get("splits"),
    )


def candidate_pairs(
    wiki_norms: set[str], rag_norms: set[str]
) -> list[tuple[str, str]]:
    """候选等价对剪枝: 只保留有表面相似性的交叉对, 交给 L3 判定。

    规则 (任一满足): 共享前缀 ≥2 字符 / 共享后缀 ≥2 字符 /
    任一非停用词 token (≥3 字符) 是对方 token 的子串 (或相反)。
    按相似度评分取前 _L3_BATCH_MAX, 避免字母序让垃圾对挤占名额。
    """
    import os as _os

    def tokens(name: str) -> set[str]:
        return {
            t.lower()
            for t in re.split(r"[^0-9a-zA-Z一-鿿]+", name)
            if t and t.lower() not in _STOP_TOKENS and len(t) >= _TOKEN_MIN_LEN
        }

    w_toks = {w: tokens(w) for w in wiki_norms}
    r_toks = {r: tokens(r) for r in rag_norms}

    def sim_score(a: str, b: str) -> float:
        """表面相似度: 前缀命中 > token 包含 > 后缀命中。"""
        score = 0.0
        if a[:2] == b[:2]:
            score += len(_os.path.commonprefix([a, b])) / max(len(a), len(b))
        ta, tb = w_toks.get(a, set()), r_toks.get(b, set())
        for x in ta:
            for y in tb:
                if x in y or y in x:
                    score += 0.5
        if a[-2:] == b[-2:]:
            score += 0.2
        return score

    scored: list[tuple[float, str, str]] = []
    for w in wiki_norms:
        for r in rag_norms:
            if w == r:
                continue  # 直接相等不占 L3 名额
            s = sim_score(w, r)
            if s > 0:
                scored.append((s, w, r))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [(w, r) for _, w, r in scored[:_L3_BATCH_MAX]]


_L3_PROMPT = """判断下列名字对是否指向同一个实体 (同一组件/接口/服务/概念, 只是写法不同)。
注意: 名字像但不是同一层级/不是同一概念的不算等价。
只输出 JSON 数组, 每项 {{"a": ..., "b": ..., "same": true/false, "confidence": 0.0-1.0}}。

# 名字对
{json_pairs}
"""


async def resolve_llm_pairs(
    pairs: list[tuple[str, str]], config: Config
) -> set[tuple[str, str]]:
    """L3: 一次批量 LLM 调用判定等价对。仅返回高置信 same=true 的归一化对。
    失败或低置信返回空集 (保持差异呈现, 不冒险合并)。"""
    if not pairs or not config.llm_api_key:
        return set()
    body = json.dumps(
        [{"a": a, "b": b} for a, b in pairs], ensure_ascii=False
    )
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{config.llm_base}/chat/completions",
                headers={"Authorization": f"Bearer {config.llm_api_key}"},
                json={
                    "model": config.llm_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": _L3_PROMPT.format(json_pairs=body),
                        }
                    ],
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError):
        return set()

    # 逐项解析: 按输入顺序对齐 LLM 输出 (输出不完整时按位置截断)
    items = re.findall(r"\{[^{}]*\}", content)
    judged: set[tuple[str, str]] = set()
    for i, (a, b) in enumerate(pairs):
        if i >= len(items):
            break
        m = re.search(
            r'"same"\s*:\s*(true|false)',
            items[i],
        )
        cm = re.search(r'"confidence"\s*:\s*([\d.]+)', items[i])
        if not m or not cm:
            continue
        if m.group(1) != "true":
            continue
        try:
            conf = float(cm.group(1))
        except ValueError:
            continue
        if conf >= _L3_CONFIDENCE_MIN:
            judged.add((a, b))
    return judged
