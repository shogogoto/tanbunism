"""quiz domain test.

多段関係 ex. この前提の前提はどれ
複数選択がマルになるケースにも対応する.
"""

import uuid
from datetime import datetime

import pytest

from tanbun.feature.domain.datetime import TZ
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.tree2net import parse2net
from tanbun.feature.quiz.domain.parts import QuizRel
from tanbun.feature.quiz.errors import InvalidAnswerOptionError, QuizDuplicateError

from .domain import QuizOption, QuizSource, QuizType


def test_duplicate_source():
    """重複チェック."""
    op = QuizOption.create("aaa", ["A"])
    with pytest.raises(QuizDuplicateError):
        QuizSource(
            quiz_id=uuid.uuid4(),
            quiz_type=QuizType.SENT2TERM,
            target_id="1",
            sources=dict.fromkeys(["1", "2"], op),
            created=datetime.now(tz=TZ),
        )


@pytest.fixture
def sn() -> SysNet:
    """用語は5個しかない."""
    s = """
        # title
            A: aaa
            B: bbb
            D: ddd
            parent
                C: ccc
                    T1: ccc1
                    T2: ccc2
                    T3: ccc3
                    -> to
                        T4: todetail
                        -> ccc5
                    <- cccb
                        <- cccb1
    """
    return parse2net(s)


def test_sysnet2source(sn: SysNet):
    """sysnetからquizsourceへの変換(テストを書きやすくする)."""
    src = QuizSource(
        quiz_id=uuid.uuid4(),
        quiz_type=QuizType.REL2PAIR,
        target_id="1",  # 問いの対象
        correct_ids=["2"],
        sources={
            "1": QuizOption(val=sn.get("ccc"), rels=[]),
            "2": QuizOption(val=sn.get("ccc1"), rels=[QuizRel.DETAIL]),
            "3": QuizOption(val=sn.get("to"), rels=[QuizRel.CONCLUSION]),
            "4": QuizOption(val=sn.get("cccb"), rels=[QuizRel.PREMISE]),
            "5": QuizOption(val=sn.get("cccb1"), rels=[QuizRel.PREMISE] * 2),
            "6": QuizOption(val=sn.get("parent"), rels=[QuizRel.PARENT]),
        },
        created=datetime.now(tz=TZ),
    )
    qs = QuizSource.from_sysnet(
        sn=sn,
        qt=QuizType.REL2PAIR,
        target_stc="ccc",
        source_stcs=["ccc", "ccc1", "to", "cccb", "cccb1", "parent"],
        correct_stcs=["ccc1"],
    )
    assert src.quiz_type == qs.quiz_type
    assert src.target_id == qs.target_id
    assert src.correct_ids == qs.correct_ids
    assert src.sources == qs.sources


def test_quiz_sent2term(sn: SysNet):
    """用語当て問題."""
    src = QuizSource.from_sysnet(
        sn=sn,
        qt=QuizType.SENT2TERM,
        target_stc="aaa",
        source_stcs=["aaa", "bbb", "ccc", "ddd"],
        correct_stcs=["aaa"],
    )
    q = src.to_readable()
    assert q.statement == "文'aaa'に合う用語はどれ?"
    assert q.quiz_type is QuizType.SENT2TERM
    assert q.prompt.model_dump() == {
        "subject": "aaa",
        "object": None,
        "relations": [],
        "answer_kind": "term",
    }
    assert set(q.options.values()) == {"A", "B", "C", "D"}
    assert q.is_correct(["1"])
    assert not q.is_correct(["1", "4"])
    assert not q.is_correct(["2"])
    assert not q.is_correct(["2", "3", "4"])
    assert not q.is_correct([])
    with pytest.raises(InvalidAnswerOptionError):
        q.is_correct(["999"])


def test_quiz_term2sent(sn: SysNet):
    """単文当て問題."""
    src = QuizSource.from_sysnet(
        sn=sn,
        qt=QuizType.TERM2SENT,
        target_stc="aaa",
        source_stcs=["aaa", "bbb", "ccc", "ddd"],
        correct_stcs=["aaa"],
    )
    q = src.to_readable()
    assert set(q.options.values()) == {"aaa", "bbb", "ccc", "ddd"}
    assert q.statement == "用語'A'に合う文はどれ?"
    assert q.prompt.subject == "A"
    assert q.prompt.answer_kind == "sentence"
    assert q.is_correct(["1"])
    assert not q.is_correct(["1", "4"])
    assert not q.is_correct(["2"])
    assert not q.is_correct(["2", "3", "4"])
    assert not q.is_correct([])


def test_option_order_is_stable_and_varies_by_quiz(sn: SysNet):
    """選択肢順は同じQuizでは安定し、Quiz間では固定位置に偏らない."""
    source = QuizSource.from_sysnet(
        sn=sn,
        qt=QuizType.TERM2SENT,
        target_stc="aaa",
        source_stcs=["aaa", "bbb", "ccc", "ddd"],
        correct_stcs=["aaa"],
    )

    assert list(source.readable_options()) == list(source.readable_options())
    correct_positions = {
        list(
            source.model_copy(
                update={"quiz_id": uuid.UUID(int=quiz_number)},
            ).readable_options(),
        ).index("1")
        for quiz_number in range(1, 21)
    }
    assert len(correct_positions) > 1


def test_quiz_rel2sent_lv1(sn: SysNet):
    """クイズ対象と関係にマッチするもの当て問題(1階層)."""
    src = QuizSource.from_sysnet(
        sn=sn,
        qt=QuizType.REL2PAIR,
        target_stc="ccc",
        source_stcs=["ccc", "ccc1", "to", "cccb", "cccb1", "parent"],
        correct_stcs=["ccc1"],
    )
    # 詳細はどれか
    q = src.to_readable()
    assert q.statement == "'ccc'-[BELOW]-><?>\n<?>に入る文はどれ?"
    assert q.prompt.model_dump() == {
        "subject": "ccc",
        "object": None,
        "relations": [{"name": "BELOW", "is_forward": True}],
        "answer_kind": "sentence",
    }
    assert q.is_correct(["2"])
    assert not q.is_correct(["3"])

    src2 = src.model_copy(update={"correct_ids": ["3"]})
    # 結論はどれか
    q = src2.to_readable()
    assert q.statement == "'ccc'-[TO]-><?>\n<?>に入る文はどれ?"
    assert q.is_correct(["3"])
    assert not q.is_correct(["2", "4"])
    assert not q.is_correct(["3", "4"])

    premise = src.model_copy(update={"correct_ids": ["4"]}).to_readable()
    assert premise.statement == "<?>-[TO]->'ccc'\n<?>に入る文はどれ?"

    premise_of_premise = src.model_copy(update={"correct_ids": ["5"]}).to_readable()
    assert premise_of_premise.statement == (
        "<?>-[TO]->…-[TO]->'ccc'\n<?>に入る文はどれ?"
    )


def test_quiz_pair2rel_shows_graph_direction(sn: SysNet):
    """関係当て問題は実際の辺方向を空欄付きグラフとして示す."""
    source = QuizSource.from_sysnet(
        sn=sn,
        qt=QuizType.PAIR2REL,
        target_stc="ccc",
        source_stcs=["ccc", "to", "cccb"],
        correct_stcs=["to"],
    )

    conclusion = source.to_readable()
    assert conclusion.statement == "'ccc'-[]->'to'\n[]に入る関係はどれ?"
    assert conclusion.prompt.model_dump() == {
        "subject": "ccc",
        "object": "to",
        "relations": [{"name": None, "is_forward": True}],
        "answer_kind": "relation",
    }

    premise = source.model_copy(update={"correct_ids": ["3"]}).to_readable()
    assert premise.statement == "'cccb'-[]->'ccc'\n[]に入る関係はどれ?"

    assert QuizType.from_statemet(conclusion.statement) is QuizType.PAIR2REL
    assert QuizType.from_statemet(premise.statement) is QuizType.PAIR2REL


def test_without_correct(sn: SysNet):
    """用語当てクイズでtargetを正解にしないパターン.

    correct_idsが空の場合はtarget_idを正解にする
    correct_idsがある場合はcorrent_idを正解にする
      ただし、用語当てなどの性質上、複数の正解
    """
    src = QuizSource.from_sysnet(
        sn=sn,
        qt=QuizType.SENT2TERM,
        target_stc="aaa",
        source_stcs=["aaa", "bbb", "ccc", "ddd"],
        correct_stcs=[],
    )
    q = src.to_readable()
    assert not q.is_correct(["2"])
    assert q.is_correct([])
