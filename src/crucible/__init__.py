"""Crucible 融合检索层。

设计依据: knowledge-fusion/DESIGN-search-strategy.md
  - 查询类型 Q1 枚举(全) / Q2 机制(准) / Q3 经验(可信)
  - 合并模式 M1 并集合并 / M2 一致性比对 / M3 状态排序
  - 五原则: 倾向性由类型决定 · KB 枚举 Agent 选择 · LLM 只比对不重写 ·
            冲突不裁决 · 合并发生在查询时
"""

__version__ = "0.1.0"
