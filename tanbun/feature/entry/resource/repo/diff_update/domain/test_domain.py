"""差分更新test."""

import pytest

from tanbun.feature.domain.graph.edge_type import EdgeType
from tanbun.feature.entry.resource.repo.diff_update.errors import (
    IdentificationError,
    InvalidIdentityResolutionError,
)
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.tree2net import parse2net

from .domain import (
    create_updatediff,
    diff2sets,
    identify_updatediff_term,
    identify_updatediff_txt,
    sysnet2edges,
)


def test_sentence_diff() -> None:
    """文差分."""
    o1 = "A"
    o2 = "XXX"  # deleted
    o3 = "YYY"  # updated
    old = [o1, o2, o3]
    n1 = "A"  # remain
    n2 = "B"  # added
    n3 = "YYX"  # updated
    new = [n1, n2, n3]
    d, a, up = create_updatediff(old, new, identify_updatediff_txt)
    assert d == {o2}
    assert a == {n2}
    assert up == {o3: n3}


def test_term_diff() -> None:
    """用語差分."""
    o1 = Term.create("A")
    o2 = Term.create("XXX")  # deleted
    o3 = Term.create("YYY")  # updated
    old = [o1, o2, o3]
    n1 = Term.create("A")  # remain
    n2 = Term.create("B")  # added
    n3 = Term.create("YYX")  # updated
    new = [n1, n2, n3]
    d, a, up = create_updatediff(old, new, identify_updatediff_term)
    assert d == {o2}
    assert a == {n2}
    assert up == {o3: n3}


def test_term_diff_accepts_explicit_conflict_resolution() -> None:
    """用語の複数候補も利用者の選択で解決できる."""
    old = [Term.create("abcdef")]
    new = [Term.create("abcde1"), Term.create("abcde2")]

    result = identify_updatediff_term(
        old,
        new,
        resolutions={"abcdef": "abcde2"},
    )

    assert result == {Term.create("abcdef"): Term.create("abcde2")}


def test_sentence_diff_reports_one_to_many_conflict() -> None:
    """1つの旧文に複数の更新候補があれば自動マージしない."""
    with pytest.raises(IdentificationError) as raised:
        identify_updatediff_txt(["abcdef"], ["abcde1", "abcde2"])

    [conflict] = raised.value.conflicts
    assert conflict.original == "abcdef"
    assert {candidate.value for candidate in conflict.candidates} == {
        "abcde1",
        "abcde2",
    }


def test_sentence_diff_reports_many_to_one_conflict() -> None:
    """複数の旧文が同じ更新先へ集まる場合も自動マージしない."""
    with pytest.raises(IdentificationError) as raised:
        identify_updatediff_txt(["abcde1", "abcde2"], ["abcdef"])

    assert {conflict.original for conflict in raised.value.conflicts} == {
        "abcde1",
        "abcde2",
    }
    assert raised.value.status_code == 409  # noqa: PLR2004
    assert raised.value.detail["type"] == "identity_conflict"
    assert raised.value.detail["kind"] == "sentence"


def test_sentence_diff_automatically_pairs_clear_reciprocal_matches() -> None:
    """複数候補があっても双方の第一候補が明確なら自動で対応付ける."""
    old_conjunction = r"$ eg(P \land eg P)$"
    old_disjunction = r"$P \lor eg P$"
    new_conjunction = r"$\neg(P \land \neg P)$"
    new_disjunction = r"$P \lor \neg P$"

    result = identify_updatediff_txt(
        [old_conjunction, old_disjunction],
        [new_conjunction, new_disjunction],
    )

    assert result == {
        old_conjunction: new_conjunction,
        old_disjunction: new_disjunction,
    }


def test_sentence_diff_applies_explicit_resolution() -> None:
    """曖昧な候補でもユーザーが選んだ1文だけへ同定する."""
    result = identify_updatediff_txt(
        ["abcdef"],
        ["abcde1", "abcde2"],
        resolutions={"abcdef": "abcde2"},
    )

    assert result == {"abcdef": "abcde2"}


def test_sentence_diff_can_explicitly_delete_original() -> None:
    """旧文を更新扱いにせず、削除と新規追加に分けられる."""
    result = identify_updatediff_txt(
        ["abcdef"],
        ["abcde1", "abcde2"],
        resolutions={"abcdef": None},
    )

    assert result == {}


@pytest.mark.parametrize(
    "resolutions",
    [
        {"not removed": "abcde1"},
        {"abcdef": "not added"},
    ],
)
def test_sentence_diff_rejects_stale_resolution(
    resolutions: dict[str, str],
) -> None:
    """現在の差分へ適用できない古い解決指定を拒否する."""
    with pytest.raises(InvalidIdentityResolutionError):
        identify_updatediff_txt(
            ["abcdef"],
            ["abcde1", "abcde2"],
            resolutions=resolutions,
        )


def test_sentence_diff_rejects_many_to_one_resolution() -> None:
    """明示指定でも複数UIDを1つへ潰さない."""
    with pytest.raises(InvalidIdentityResolutionError):
        identify_updatediff_txt(
            ["abcde1", "abcde2"],
            ["abcdef"],
            resolutions={"abcde1": "abcdef", "abcde2": "abcdef"},
        )


def test_edgediff() -> None:
    """関係の変更."""
    txt1 = """
        # title
            A: aaa
            B: bbb
            C: ccc
            ddd
    """

    txt2 = """
        # title
            A: aaa
                BB: bbb
            C: ccc
            ddd
    """
    sn1 = parse2net(txt1)
    sn2 = parse2net(txt2)
    e1 = sysnet2edges(sn1)
    e2 = sysnet2edges(sn2)
    removed, added = diff2sets(e1, e2)
    assert removed == {
        ("aaa", "bbb", EdgeType.SIBLING),
        ("bbb", "ccc", EdgeType.SIBLING),
    }
    assert added == {
        ("aaa", "bbb", EdgeType.BELOW),
        ("aaa", "ccc", EdgeType.SIBLING),
    }
