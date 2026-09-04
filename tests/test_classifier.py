"""判别器规则层单测 (纯函数, 无网络)。"""
from crucible.classifier import classify_by_rules, matched_rule, merge_mode
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


"""多轮追问改写: 检测 + 降级路径 (纯函数, 无网络)。"""
import asyncio

from crucible.classifier import _is_followup, rewrite_if_needed
from crucible.config import Config


def test_is_followup_true():
    for q in [
        "那这个接口呢",
        "它的鉴权怎么做",
        "上述组件有哪些",
        "接口呢",
        "那它的验证结果呢？",
    ]:
        assert _is_followup(q), q


def test_is_followup_false():
    for q in [
        "MAE 有哪些组件？",
        "hiro 总线是什么？",
        "鉴权在哪一层做的？",
        "文件名是怎么进入 convert 命令的？",
    ]:
        assert not _is_followup(q), q


def test_rewrite_no_history_returns_original():
    # 无历史: 不触发改写, 且不发网络请求
    result = asyncio.run(rewrite_if_needed("那这个接口呢", None, Config()))
    assert result == "那这个接口呢"


def test_rewrite_no_followup_returns_original():
    result = asyncio.run(
        rewrite_if_needed("MAE 有哪些组件？", ["MAE 有哪些接口？"], Config())
    )
    assert result == "MAE 有哪些组件？"


def test_rewrite_llm_unavailable_degrades_to_original():
    # 无 LLM key: 改写失败, 原样降级 (不发网络请求)
    result = asyncio.run(
        rewrite_if_needed(
            "那这个接口呢", ["MAE 有哪些接口？"], Config(llm_api_key="")
        )
    )
    assert result == "那这个接口呢"


def test_experience_weak_rule_after_mechanism():
    # 裸「验证」: 无机制信号 → Q3
    result = classify_by_rules("上传验证")
    assert result is not None and result.query_type == QueryType.EXPERIENCE
    # 「如何验证」: 机制信号优先 → Q2
    result2 = classify_by_rules("如何验证接口")
    assert result2 is not None and result2.query_type == QueryType.MECHANISM


def test_matched_rule():
    assert matched_rule("有哪些组件") == r"哪些|有哪些|类型|清单|列举|列出|全部|多少(个|种|类)?"
    assert matched_rule("上传验证") == r"验证|验证记录|实测记录|误报"
    assert matched_rule("完全无关的一句话") is None
