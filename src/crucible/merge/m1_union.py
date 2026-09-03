"""M1 并集合并 —— 服务于 Q1 枚举型 (全)。

两库枚举结果 → 去重并集 + 差异清单 (设计文档 §5.1):
  - union: 归一化名称并集
  - differences: only_wiki / only_rag / 冲突, 附回填建议
差异清单即免费失真检测: 两引擎独立提取, 不一致处即可疑点。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..schemas import Difference


def normalize_name(raw: str) -> str:
    """名称归一化: 去 .md、去路径前缀、NFKC、小写、空白折叠。

    wiki 侧给的是页面相对路径 (concepts/代码制品l4), rag 侧给的是实体名
    (Code Artifact)。归一化到「最后一节小写」以可比对。
    """
    name = str(raw).replace(".md", "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = name.strip()
    # NFKC 折叠全角
    import unicodedata

    name = unicodedata.normalize("NFKC", name)
    return re.sub(r"\s+", " ", name).lower()


@dataclass
class UnionResult:
    union: list[str] = field(default_factory=list)
    differences: list[Difference] = field(default_factory=list)
    wiki_items: list[str] = field(default_factory=list)
    rag_items: list[str] = field(default_factory=list)
    alias_notes: list[str] = field(default_factory=list)  # L2/L3 命中记录


def union_merge(
    wiki_names: list[str],
    rag_names: list[str],
    *,
    wiki_side_label: str = "wiki",
    rag_side_label: str = "rag",
    aliases=None,                      # merge.aliases.AliasDict | None
    llm_same: set[tuple[str, str]] | None = None,  # L3 判定等价 (norm, norm)
) -> UnionResult:
    """并集合并。匹配判定顺序 (decisions.md D-05/D-08):

    1. 归一化名直接相等
    2. L2 等价组 (aliases.equivalences)
    3. L2 复合拆分吸收 (wiki 复合页 ↔ rag 原子实体)
    4. L3 LLM 判定 (llm_same)

    命中任意一级即视为同一实体: 不产生差异项; 拆分吸收时
    原子名不再单列 (被复合名覆盖)。
    """
    wiki_norm = {normalize_name(n): n for n in wiki_names}
    rag_norm = {normalize_name(n): n for n in rag_names}

    alias_notes: list[str] = []
    matched_wiki: set[str] = set()   # 已与 rag 侧等价的 wiki 名 (不产生差异)
    matched_rag: set[str] = set()    # 已与 wiki 侧等价的 rag 名 (不产生差异)
    absorbed_rag: set[str] = set()   # 被复合名吸收的 rag 原子 (union 不单列)
    absorbed_wiki: set[str] = set()  # 被复合名吸收的 wiki 原子

    # 1. 直接相等
    direct = set(wiki_norm) & set(rag_norm)
    matched_wiki |= direct
    matched_rag |= direct

    # 2. L2 等价组
    if aliases is not None:
        for w in set(wiki_norm) - matched_wiki:
            eq = aliases.equivalences(w) & set(rag_norm)
            if eq:
                matched_wiki.add(w)
                matched_rag |= eq
                alias_notes.append(
                    f"alias_group: {w} ↔ {', '.join(sorted(eq))}"
                )

    # 3. L2 复合拆分吸收 (双向: wiki 复合吸收 rag 原子 / rag 复合吸收 wiki 原子)
    if aliases is not None:
        for w in set(wiki_norm) - matched_wiki:
            atoms = aliases.split_atoms(w) & set(rag_norm)
            if atoms:
                matched_wiki.add(w)
                absorbed_rag |= atoms
                alias_notes.append(
                    f"alias_split: {w} 吸收 {', '.join(sorted(atoms))}"
                )
        for r in set(rag_norm) - matched_rag:
            atoms = aliases.split_atoms(r) & set(wiki_norm)
            if atoms:
                matched_rag.add(r)
                absorbed_wiki |= atoms
                alias_notes.append(
                    f"alias_split: {r} 吸收 {', '.join(sorted(atoms))}"
                )

    # 4. L3 LLM 判定
    if llm_same:
        for w, r in llm_same:
            if w in wiki_norm and r in rag_norm and w not in matched_wiki:
                matched_wiki.add(w)
                matched_rag.add(r)
                alias_notes.append(f"alias_llm: {w} ↔ {r}")

    union: list[str] = []
    differences: list[Difference] = []

    for norm, original in sorted(wiki_norm.items()):
        if norm in matched_wiki:
            union.append(original)
        elif norm in absorbed_wiki:
            continue  # 被 rag 复合名覆盖, 不单列不差异
        else:
            union.append(original)
            differences.append(
                Difference(
                    item=original,
                    only_in=wiki_side_label,
                    action=f"检查 {rag_side_label} 抽取为何遗漏该实体",
                )
            )

    for norm, original in sorted(rag_norm.items()):
        if norm in absorbed_rag:
            continue  # 被 wiki 复合名覆盖
        if norm in matched_rag:
            continue  # 与 wiki 侧等价, union 已含 wiki 侧原名
        union.append(original)
        differences.append(
            Difference(
                item=original,
                only_in=rag_side_label,
                action=f"回填 {wiki_side_label} 清单页",
            )
        )

    return UnionResult(
        union=union,
        differences=differences,
        wiki_items=list(wiki_norm.values()),
        rag_items=list(rag_norm.values()),
        alias_notes=alias_notes,
    )
