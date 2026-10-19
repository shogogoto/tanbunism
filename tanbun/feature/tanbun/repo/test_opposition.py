"""反対関係は対称・非推移。表示、重要度、出題で同じ関係を利用する。."""

from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.domain.types import to_uuid
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.quiz.candidate.types import CandidateType
from tanbun.feature.quiz.domain.parts import QuizType
from tanbun.feature.quiz.generation.repo import generate_quiz
from tanbun.feature.quiz.learning.selection.repo import fetch_uncovered_relation_pairs
from tanbun.feature.tanbun.label import LSentence
from tanbun.feature.tanbun.repo import adj_tanbun, search_tanbun
from tanbun.feature.tanbun.repo.clause import OrderBy
from tanbun.feature.tanbun.repo.detail import (
    fetch_tanbun_chains,
    fetch_tanbuns_with_detail,
)
from tanbun.feature.user.label import LUser


async def opposition_fixture():
    """パーサーが保存した関係に、重複と連鎖を加える。."""
    user = await LUser(email="opposition@example.com").save()
    _, resource = await save_text(
        user.uid,
        """
    # contrast
        楽観: 未来を明るく見る
            <-> 悲観: 未来を暗く見る
        保留: 未来をまだ判断しない
        結論: 判断には根拠が必要である
    """,
    )
    nodes = {
        name: await LSentence.nodes.get(val=value)
        for name, value in {
            "a": "未来を明るく見る",
            "b": "未来を暗く見る",
            "c": "未来をまだ判断しない",
            "d": "判断には根拠が必要である",
        }.items()
    }
    await adb.cypher_query(
        """
        MATCH (a:Sentence {uid:$a}), (b:Sentence {uid:$b}),
              (c:Sentence {uid:$c}), (d:Sentence {uid:$d})
        CREATE (b)-[:ANTI]->(a), (b)-[:ANTI]->(c), (b)-[:TO]->(d)
        """,
        {key: node.uid for key, node in nodes.items()},
    )
    return user, resource, nodes


@mark_async_test()
async def test_opposition_counts_unique_direct_partners_with_configurable_weight():
    """両端に加点するが逆向き重複・間接関係では加点を増やさない。."""
    _, resource, nodes = await opposition_fixture()
    ids = [node.uid for node in nodes.values()]
    weighted = await fetch_tanbuns_with_detail(ids, OrderBy(n_anti=3))
    baseline = await fetch_tanbuns_with_detail(ids, OrderBy(n_anti=0))
    for name, count in {"a": 1, "b": 2, "c": 1, "d": 0}.items():
        uid = nodes[name].uid
        assert weighted[uid].stats.n_anti == count
        assert weighted[uid].stats.score - baseline[uid].stats.score == 3 * count
    result = await search_tanbun(
        "",
        order_by=OrderBy(n_anti=5),
        filter_resource_uids=[resource.uid],
    )
    assert result.data[0].uid == to_uuid(nodes["b"].uid)


@mark_async_test()
async def test_opposition_detail_is_symmetric_and_not_transitive():
    """逆向きでも詳細で見えるが、反対の反対を直接の反対としては出さない。."""
    _, resource, nodes = await opposition_fixture()
    for name, expected in {"a": {"b"}, "b": {"a", "c"}, "c": {"b"}}.items():
        chain = (await fetch_tanbun_chains([nodes[name].uid])).root[0]
        assert chain.location.resource.uid == resource.uid
        opponents = {
            end if start == nodes[name].uid else start
            for start, end, data in chain.g.edges(data=True)
            if data["type"] == "anti"
        }
        assert opponents == {nodes[key].uid for key in expected}
        adjacency = (await adj_tanbun([nodes[name].uid], radius=10))[0]
        assert {to_uuid(node.uid) for node in adjacency.antis} == {
            to_uuid(nodes[key].uid) for key in expected
        }


@mark_async_test()
async def test_opposition_enters_automatic_candidates_and_both_relation_quiz_forms():
    """反対の単文を選ぶ問題と、二つの単文の関係を選ぶ問題を復元できる。."""
    user, resource, nodes = await opposition_fixture()
    pairs = await fetch_uncovered_relation_pairs(
        resource.uid,
        user.uid,
        QuizType.REL2PAIR,
        limit=100,
    )
    assert (to_uuid(nodes["a"].uid), to_uuid(nodes["b"].uid)) in pairs
    assert (to_uuid(nodes["b"].uid), to_uuid(nodes["a"].uid)) in pairs
    for target, correct in [("a", "b"), ("b", "a")]:
        quiz = await generate_quiz(
            QuizType.REL2PAIR,
            CandidateType.ALL,
            nodes[target].uid,
            3,
            user.uid,
            correct_sent_uids=[nodes[correct].uid],
        )
        readable = quiz.to_readable()
        assert readable.prompt.relations[0].name == "ANTI"
        assert "<-[ANTI]->" in readable.statement
        assert readable.is_correct([nodes[correct].uid])
    quiz = await generate_quiz(
        QuizType.PAIR2REL,
        CandidateType.ALL,
        nodes["b"].uid,
        2,
        user.uid,
        correct_sent_uids=[nodes["a"].uid],
    )
    readable = quiz.to_readable()
    assert readable.options[nodes["a"].uid] == "反対"
    assert all(relation.name is None for relation in readable.prompt.relations)
    assert "ANTI" not in readable.statement
    assert "<->" not in readable.statement
    assert "<-[]->" not in readable.statement
