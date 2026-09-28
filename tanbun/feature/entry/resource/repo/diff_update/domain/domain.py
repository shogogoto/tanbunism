"""差分更新domain."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from itertools import product
from typing import TYPE_CHECKING

import Levenshtein

from tanbun.feature.domain.types import Duplicable
from tanbun.feature.entry.resource.repo.diff_update.errors import (
    IdentificationError,
    IdentityCandidate,
    IdentityConflict,
    IdentityKind,
    InvalidIdentityResolutionError,
)
from tanbun.feature.entry.resource.repo.save import EdgeRel
from tanbun.feature.parsing.sysnet.sysnode import Def, Sentency

if TYPE_CHECKING:
    from tanbun.feature.parsing.primitive.term import Term
    from tanbun.feature.parsing.sysnet import SysNet

type UpdateGetter[T] = Callable[[Iterable[T], Iterable[T]], dict[T, T]]


def identify_updatediff_txt(
    old: Iterable[str],
    new: Iterable[str],
    threshold_ratio: float = 0.6,
    resolutions: dict[str, str | None] | None = None,
    kind: IdentityKind = "sentence",
) -> dict[Sentency, Sentency]:
    """2種類の文の集合の更新対を同定."""
    o, n = set(old), set(new)
    removed, added = o - n, n - o
    resolutions = resolutions or {}
    resolved = _validate_resolutions(removed, added, resolutions)
    removed -= set(resolutions)
    added -= set(resolved.values())

    candidates_by_old: dict[str, list[IdentityCandidate]] = defaultdict(list)
    olds_by_new: dict[str, list[str]] = defaultdict(list)
    for txt1, txt2 in product(removed, added):
        r = Levenshtein.ratio(txt1, txt2)
        if r > threshold_ratio:
            candidates_by_old[txt1].append(IdentityCandidate(txt2, r))
            olds_by_new[txt2].append(txt1)

    ambiguous_old = {
        old for old, candidates in candidates_by_old.items() if len(candidates) > 1
    }
    ambiguous_old.update(
        old for olds in olds_by_new.values() if len(olds) > 1 for old in olds
    )
    if ambiguous_old:
        conflicts = tuple(
            IdentityConflict(
                original=old,
                candidates=tuple(
                    sorted(
                        candidates_by_old[old],
                        key=lambda candidate: (-candidate.similarity, candidate.value),
                    ),
                ),
            )
            for old in sorted(ambiguous_old)
        )
        raise IdentificationError(conflicts, kind=kind)

    automatic = {
        old: candidates[0].value
        for old, candidates in candidates_by_old.items()
        if candidates
    }
    return resolved | automatic


def _validate_resolutions(
    removed: set[str],
    added: set[str],
    resolutions: dict[str, str | None],
) -> dict[str, str]:
    """明示された解決内容が現在の差分に適用できることを検証する."""
    unknown_originals = set(resolutions) - removed
    if unknown_originals:
        values = "、".join(sorted(unknown_originals))
        msg = f"削除対象ではない旧文です: {values}"
        raise InvalidIdentityResolutionError(msg)

    replacements = [value for value in resolutions.values() if value is not None]
    unknown_replacements = set(replacements) - added
    if unknown_replacements:
        values = "、".join(sorted(unknown_replacements))
        msg = f"追加対象ではない新文です: {values}"
        raise InvalidIdentityResolutionError(msg)
    if len(replacements) != len(set(replacements)):
        msg = "複数の旧文を同じ新文へ同定することはできません"
        raise InvalidIdentityResolutionError(
            msg,
        )
    return {
        original: replacement
        for original, replacement in resolutions.items()
        if replacement is not None
    }


def diff2sets[T](old: Iterable[T], new: Iterable[T]) -> tuple[set[T], set[T]]:
    """差分をsetに変換."""
    o, n = set(old), set(new)
    removed, added = o - n, n - o
    return removed, added


def create_updatediff[T](
    old: Iterable[T],
    new: Iterable[T],
    f: UpdateGetter[T],
) -> tuple[set[T], set[T], dict[T, T]]:
    """更新差分の作成."""
    rm, add = diff2sets(old, new)
    updated = f(rm, add)
    rm -= set(updated.keys())
    add -= set(updated.values())
    return rm, add, updated


def identify_updatediff_term(
    old: Iterable[Term],
    new: Iterable[Term],
    threshold_ratio: float = 0.6,
    resolutions: dict[str, str | None] | None = None,
) -> dict[Term, Term]:
    """2種類の用語の集合の更新対を同定."""
    r_txts = {str(t): t for t in old}
    a_txts = {str(t): t for t in new}
    updiff = identify_updatediff_txt(
        r_txts.keys(),
        a_txts.keys(),
        threshold_ratio,
        resolutions,
        kind="term",
    )
    return {r_txts[k]: a_txts[v] for k, v in updiff.items()}


def sysnet2edges(sn: SysNet) -> set[EdgeRel]:
    """edgeをsetに変換."""
    edges = set()
    for s in [*sn.sentences, sn.root]:  # 単文の関係だけ見れば良い
        e = {(u, v, attr["type"]) for u, v, attr in sn.g.out_edges(s, data=True)}
        edges = edges.union(e)
    return edges


def edges2nodes(es: Iterable[EdgeRel]) -> set[Sentency]:
    """edgeをnodeに変換."""
    s = set()
    for e in es:
        u, v, _ = e
        s.add(u)
        s.add(v)
    return s


def get_switched_def_terms(old: SysNet, upd: SysNet) -> dict[Term, Term]:
    """用語と単文の入れ替えを取得する.

    単文はそのままにしたいので、操作すべきtermsを返す
    """
    old_defs = {old.get(t) for t in old.terms}
    old_defs = {d for d in old_defs if isinstance(d, Def)}  # 型付け
    d = {}
    for old_d in old_defs:
        if old_d.sentence not in upd.g:
            continue
        upd_d = upd.get(old_d.sentence)
        if isinstance(upd_d, Def) and upd_d.term != old_d.term:
            d[old_d.term] = upd_d.term
    return d


def identify_duplicate_updiff(
    old: SysNet,
    upd: SysNet,
) -> dict[Sentency, Sentency]:
    """Dupl <-> strの変更を用語共有で同定."""
    map_ = {}
    # Dupl -> str
    old_d = [
        d
        for d in [old.get(s) for s in old.sentences]
        if isinstance(d, Def) and isinstance(d.sentence, Duplicable)
    ]
    for od in old_d:
        if od.term not in upd.g:
            continue
        d = upd.get(od.term)
        if isinstance(d, Def) and od.sentence != d.sentence:
            map_[od.sentence] = d.sentence

    # str -> Dupl
    upd_d = [
        d
        for d in [upd.get(s) for s in upd.sentences]
        if isinstance(d, Def) and isinstance(d.sentence, Duplicable)
    ]
    for ud in upd_d:
        if ud.term not in old.g:
            continue
        d = old.get(ud.term)
        if isinstance(d, Def) and ud.sentence != d.sentence:
            map_[d.sentence] = ud.sentence
    return map_
