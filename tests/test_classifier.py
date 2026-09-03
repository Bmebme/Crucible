"""判别器规则层单测 (纯函数, 无网络)。"""
from crucible.classifier import classify_by_rules, merge_mode
from crucible.schemas import QueryType


def test_enum_rules():
    for q in [
        "这个产品有哪些文件处理组件？",
        "消息类型有哪些？",
        "列出所有外部接口",
        "这个模块暴露哪些接口？",
    ]:
        result = classify_by_rules(q)
        assert result is not None and result.query_type == QueryType.ENUM, q


def test_mechanism_rules():
    for q in [
        "文件名是怎么进入 convert 命令的？",
        "鉴权在哪一层做的？",
        "MAE 的 hiro 总线是什么？",
        "控制方向和调用方向有什么区别？",
    ]:
        result = classify_by_rules(q)
        assert result is not None and result.query_type == QueryType.MECHANISM, q


def test_experience_rules():
    for q in [
        "以前这类场景怎么打的？",
        "上次 SSRF 验证是被什么拦的？",
        "这个 payload 验证成功过吗？",
    ]:
        result = classify_by_rules(q)
        assert result is not None and result.query_type == QueryType.EXPERIENCE, q


def test_rule_channels():
    result = classify_by_rules("有哪些组件")
    assert result is not None
    assert result.channels == ["wiki", "rag"]


def test_merge_mode_mapping():
    assert merge_mode(QueryType.ENUM) == "M1"
    assert merge_mode(QueryType.MECHANISM) == "M2"
    assert merge_mode(QueryType.EXPERIENCE) == "M3"
