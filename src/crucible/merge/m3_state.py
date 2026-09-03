"""M3 状态排序 —— 服务于 Q3 经验型 (可信)。

不合并内容: 历史结果原样引用, 只按 verify_state 加权排序 (设计文档 §5.3)。

权重表:
  verified_success    1.0   置顶, 标注验证时间与环境
  unverified          0.5   标注「未经实测」
  verified_blocked    0.2   仅当当前环境与记录环境匹配时作为负知识返回
  false_positive      0.0   除非查询正是「为什么是误报」
"""
from __future__ import annotations

from dataclasses import dataclass

_WEIGHTS = {
    "verified_success": 1.0,
    "unverified": 0.5,
    "verified_blocked": 0.2,
    "false_positive": 0.0,
}

VERIFY_STATES = tuple(_WEIGHTS)


@dataclass
class SortedHit:
    item: dict
    state: str
    weight: float
    note: str = ""


def _state_of(item: dict) -> str:
    state = str(item.get("verify_state") or "unverified")
    return state if state in _WEIGHTS else "unverified"


def sort_by_verify_state(
    items: list[dict],
    *,
    env: str = "",
    query: str = "",
) -> list[SortedHit]:
    """按 verify_state 加权排序。blocked 仅在环境匹配时作为负知识返回;
    false_positive 仅在查询明确指向误报时返回。"""
    hits: list[SortedHit] = []
    for item in items:
        state = _state_of(item)
        weight = _WEIGHTS[state]
        note = ""
        if state == "unverified":
            note = "未经实测"
        if state == "verified_blocked":
            record_env = str(item.get("verify_env") or "")
            if record_env and env and record_env != env:
                weight = 0.0  # 环境不符: 不返回
                note = f"环境不符 ({record_env} != {env})，跳过"
            else:
                note = f"负知识（拦截特征）· 环境 {record_env or '未标注'}"
        if state == "false_positive":
            if "误报" not in query and "为什么" not in query:
                weight = 0.0
                note = "误报记录，仅误报排查时返回"
            else:
                note = "误报记录"
        hits.append(SortedHit(item=item, state=state, weight=weight, note=note))

    # 稳定排序: 权重降序; 权重 0 的放末尾保留 note
    ordered = sorted(hits, key=lambda h: h.weight, reverse=True)
    return ordered
