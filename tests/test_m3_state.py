"""M3 状态排序单测 (纯函数)。"""
from crucible.merge.m3_state import sort_by_verify_state


def _item(state, **extra):
    return {"path": f"wiki/verification/{state}.md", "verify_state": state, **extra}


def test_success_ranks_first():
    ordered = sort_by_verify_state(
        [_item("unverified"), _item("verified_success"), _item("unverified")]
    )
    assert ordered[0].state == "verified_success"
    assert ordered[0].weight == 1.0


def test_unverified_weight_half():
    ordered = sort_by_verify_state([_item("unverified")])
    assert ordered[0].weight == 0.5
    assert "未经实测" in ordered[0].note


def test_blocked_negative_knowledge_env_match():
    ordered = sort_by_verify_state(
        [_item("verified_blocked", verify_env="staging")], env="staging"
    )
    assert len(ordered) == 1
    assert "负知识" in ordered[0].note


def test_blocked_env_mismatch_filtered():
    ordered = sort_by_verify_state(
        [_item("verified_blocked", verify_env="staging")], env="prod"
    )
    assert ordered[0].weight == 0.0


def test_false_positive_only_for_误报排查():
    ordered = sort_by_verify_state([_item("false_positive")], query="上传接口有哪些参数")
    assert ordered[0].weight == 0.0
    ordered = sort_by_verify_state([_item("false_positive")], query="为什么这个是误报")
    assert ordered[0].weight == 0.0  # false_positive 权重恒 0, 仅 note 区分


def test_content_not_rewritten():
    item = _item("verified_success", title="历史 POC 记录原文")
    ordered = sort_by_verify_state([item])
    assert ordered[0].item == item  # 原样引用, 不改写
