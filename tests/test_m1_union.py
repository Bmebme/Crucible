"""M1 并集合并单测 (纯函数)。"""
from crucible.merge.m1_union import normalize_name, union_merge


def test_normalize_strips_md_and_path():
    assert normalize_name("concepts/代码制品l4.md") == "代码制品l4"
    assert normalize_name("concepts/代码制品l4") == "代码制品l4"


def test_union_merge_both_sides():
    result = union_merge(
        ["concepts/服务架构l3.md", "concepts/MAE部署架构.md"],
        ["Service Architecture", "EMS"],
    )
    assert len(result.union) == 4
    # 无同名归一化命中 → 全部进入差异清单
    assert len(result.differences) == 4


def test_union_merge_dedupe_same_name():
    result = union_merge(["kafka-pipeline"], ["kafka-pipeline"])
    assert len(result.union) == 1
    assert result.differences == []


def test_union_merge_normalized_match():
    # 大小写/空格归一化后同名 → 合并, 不产生差异
    result = union_merge(["Kafka Pipeline"], ["kafka pipeline"])
    assert len(result.union) == 1
    assert result.differences == []


def test_difference_actions():
    result = union_merge(["only-wiki"], ["only-rag"])
    by_item = {d.item: d for d in result.differences}
    # rag 独有 → 回填 wiki 清单页; wiki 独有 → 检查 rag 抽取为何遗漏
    assert by_item["only-rag"].only_in == "rag"
    assert "回填" in by_item["only-rag"].action
    assert by_item["only-wiki"].only_in == "wiki"
    assert "遗漏" in by_item["only-wiki"].action
