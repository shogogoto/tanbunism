"""更新差分の組み立て."""

from collections.abc import Iterable

from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.sysnode import Sentency

from .domain import (
    create_updatediff,
    get_switched_def_terms,
    identify_duplicate_updiff,
    identify_updatediff_term,
    identify_updatediff_txt,
)


def build_sentency_updiff(
    old_sn: SysNet,
    upd_sn: SysNet,
    resolutions: dict[str, str | None] | None = None,
) -> tuple[set[Sentency], set[Sentency], dict[Sentency, Sentency]]:
    """単文更新差分を作る."""

    def f(old: Iterable[Sentency], new: Iterable[Sentency]):
        updiff = identify_updatediff_txt(
            [o for o in old if isinstance(o, str)],
            [n for n in new if isinstance(n, str)],
            threshold_ratio=0.6,
            resolutions=resolutions,
        )
        updiff |= identify_duplicate_updiff(old_sn, upd_sn)
        return updiff

    return create_updatediff(
        old_sn.sentences + old_sn.whens,
        upd_sn.sentences + upd_sn.whens,
        f,
    )


def build_term_updiff(
    old_sn: SysNet,
    upd_sn: SysNet,
    resolutions: dict[str, str | None] | None = None,
) -> tuple[set[Term], set[Term], dict[Term, Term]]:
    """用語更新差分を作る."""

    def f2(old: Iterable[Term], new: Iterable[Term]):
        updiff = identify_updatediff_term(
            [o for o in old if isinstance(o, Term)],
            [n for n in new if isinstance(n, Term)],
            threshold_ratio=0.6,
            resolutions=resolutions,
        )
        updiff |= get_switched_def_terms(old_sn, upd_sn)
        return updiff

    return create_updatediff(old_sn.terms, upd_sn.terms, f2)
