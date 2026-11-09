"""入力に依存する解析キャッシュの容量と所有権."""

import gc
import weakref

import networkx as nx
import pytest

from tanbun.feature.parsing import tree2net
from tanbun.feature.parsing.primitive.mark import split_pattern
from tanbun.feature.parsing.primitive.time.parse import str2edtf
from tanbun.feature.parsing.tree2net import _cached_parse2net
from tanbun.feature.parsing.tree2net.transformer import TSysArg
from tanbun.feature.parsing.tree_parse import PARSER_CACHE_MAX_ENTRIES, create_parser


@pytest.fixture(autouse=True)
def clear_caches():
    """実行順に依存せず、他のテストへキャッシュ状態を持ち越さない."""
    caches = (_cached_parse2net, create_parser, split_pattern, str2edtf)
    for cache in caches:
        cache.cache_clear()
    yield
    for cache in caches:
        cache.cache_clear()


def test_parse_cache_reuses_work_but_returns_isolated_results(mocker) -> None:
    """グラフ属性・派生リスト・親テーブルの変更が次回へ混ざらない."""
    source = "# cache\n  parent\n    child\n"
    parse = mocker.spy(tree2net, "parse2net_uncached")
    first = tree2net.parse2net(source)
    first.g.nodes["child"]["local"] = "changed"
    first.g.graph["local"] = "changed"
    first.sentences.clear()
    first.parents["child"] = "changed"
    second = tree2net.parse2net(source)
    assert parse.call_count == 1
    assert second is not first
    assert second.g is not first.g
    assert "local" not in second.g.nodes["child"]
    assert "local" not in second.g.graph
    assert set(second.sentences) == {"parent", "child"}
    assert second.parents["child"] == "parent"
    with pytest.raises(nx.NetworkXError):
        second.g.add_node("new")


def test_parse_cache_evicts_old_inputs_and_releases_their_graphs() -> None:
    """長時間稼働しても古い入力と解析グラフを無制限に保持しない."""
    source = "# oldest\n  value\n"
    snapshot = _cached_parse2net(source)
    reference = weakref.ref(snapshot.g)
    del snapshot
    for index in range(tree2net.PARSE_CACHE_MAX_ENTRIES):
        tree2net.parse2net(f"# cache-{index}\n  value\n")
    info = _cached_parse2net.cache_info()
    assert info.currsize == tree2net.PARSE_CACHE_MAX_ENTRIES
    gc.collect()
    assert reference() is None
    tree2net.parse2net(source)
    assert _cached_parse2net.cache_info().misses == info.misses + 1


def test_large_inputs_bypass_the_parse_cache(monkeypatch, mocker) -> None:
    """大きな入力は解析できるが、本文・グラフを常駐させない."""
    source = "# large\n  value\n"
    monkeypatch.setattr(tree2net, "PARSE_CACHE_MAX_CHARS", len(source) - 1)
    parse = mocker.spy(tree2net, "parse2net_uncached")
    assert tree2net.parse2net(source).sentences == ["value"]
    assert tree2net.parse2net(source).sentences == ["value"]
    assert parse.call_count == 2  # noqa: PLR2004
    assert _cached_parse2net.cache_info().currsize == 0


def test_debug_output_is_not_suppressed_by_cache_hits(capsys) -> None:
    """表示要求は毎回実行し、通常の解析キャッシュも変更しない."""
    source = "# debug\n  value\n"
    tree2net.parse2net(source)
    tree2net.parse2net(source, do_print=True)
    assert capsys.readouterr().out
    tree2net.parse2net(source, do_print=True)
    assert capsys.readouterr().out
    assert _cached_parse2net.cache_info().misses == 1


def test_parser_cache_evicts_old_transformer_instances() -> None:
    """解析ごとに作られる変換器も有限件数だけ保持する."""
    transformer = TSysArg()
    reference = weakref.ref(transformer)
    assert create_parser(transformer) is create_parser(transformer)
    del transformer
    for _ in range(PARSER_CACHE_MAX_ENTRIES):
        create_parser(TSysArg())
    assert create_parser.cache_info().currsize == PARSER_CACHE_MAX_ENTRIES
    gc.collect()
    assert reference() is None


def test_small_immutable_results_have_bounded_caches() -> None:
    """日付変換と分割パターンも入力件数に比例して増え続けない."""
    limit = str2edtf.cache_parameters()["maxsize"]
    assert limit is not None
    for year in range(1000, 1000 + limit + 1):
        assert str2edtf(str(year)) == str(year)
    assert str2edtf.cache_info().currsize == limit
    limit = split_pattern.cache_parameters()["maxsize"]
    assert limit is not None
    for index in range(limit + 1):
        split_pattern("(", ")", str(index))
    assert split_pattern.cache_info().currsize == limit
