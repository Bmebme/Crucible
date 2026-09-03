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


def union_merge(
    wiki_names: list[str],
    rag_names: list[str],
    *,
    wiki_side_label: str = "wiki",
    rag_side_label: str = "rag",
) -> UnionResult:
    """并集合并。同名归一化命中即合并; 差异按来源标注 + 回填建议。"""
    wiki_norm = {normalize_name(n): n for n in wiki_names}
    rag_norm = {normalize_name(n): n for n in rag_names}

    union: list[str] = []
    differences: list[Difference] = []

    for norm, original in sorted(wiki_norm.items()):
        if norm in rag_norm:
            union.append(original)
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
        if norm not in wiki_norm:
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
    )
