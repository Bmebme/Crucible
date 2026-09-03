"""名字对齐 L2/L3 单测 (纯函数, 无网络)。"""
from crucible.merge.aliases import AliasDict, candidate_pairs
from crucible.merge.m1_union import normalize_name, union_merge


def test_alias_dict_equivalences():
    d = AliasDict(groups=[["hiro-路由总线", "Hiro总线"]], splits=None)
    eq = d.equivalences("hiro-路由总线")
    assert normalize_name("Hiro总线") in eq
    assert normalize_name("hiro-路由总线") in eq


def test_alias_dict_split_atoms():
    d = AliasDict(groups=None, splits={"er-ir-接口分类": ["er接口", "ir接口"]})
    atoms = d.split_atoms("er-ir-接口分类")
    assert normalize_name("er接口") in atoms
    assert d.split_atoms("不存在的复合名") == set()


def test_union_merge_group_matches():
    d = AliasDict(groups=[["hiro-路由总线", "Hiro总线"]], splits=None)
    r = union_merge(
        ["wiki/concepts/hiro-路由总线"],
        ["Hiro总线"],
        aliases=d,
    )
    assert len(r.differences) == 0
    assert any("alias_group" in n for n in r.alias_notes)


def test_union_merge_split_absorbs():
    d = AliasDict(groups=None, splits={"er-ir-接口分类": ["er接口", "ir接口"]})
    r = union_merge(
        ["wiki/concepts/er-ir-接口分类"],
        ["er接口", "ir接口"],
        aliases=d,
    )
    # 复合名在 union 里, 两个原子被吸收, 不产生差异
    assert len(r.differences) == 0
    assert "wiki/concepts/er-ir-接口分类" in r.union
    assert "er接口" not in r.union and "ir接口" not in r.union
    assert any("alias_split" in n for n in r.alias_notes)


def test_union_merge_llm_same():
    r = union_merge(
        ["wiki/concepts/nginx"],
        ["nginx-proxy"],
        llm_same={(normalize_name("nginx"), normalize_name("nginx-proxy"))},
    )
    assert len(r.differences) == 0
    assert any("alias_llm" in n for n in r.alias_notes)


def test_union_merge_without_aliases_keeps_old_behavior():
    # 无对齐参数: 行为与旧版一致 (差异全量呈现)
    r = union_merge(["wiki/concepts/er-ir-接口分类"], ["er接口", "ir接口"])
    assert len(r.differences) == 3
    assert r.alias_notes == []


def test_candidate_pairs_surface_heuristics():
    wiki = {normalize_name("hiro-路由总线"), normalize_name("a610-22-机柜")}
    rag = {normalize_name("Hiro总线"), normalize_name("taishan-200-server")}
    pairs = candidate_pairs(wiki, rag)
    # hiro 共享前缀命中; 机柜/服务器无表面相似
    assert (normalize_name("hiro-路由总线"), normalize_name("Hiro总线")) in pairs
    assert not any(
        "a610" in a for a, _ in pairs
    )
