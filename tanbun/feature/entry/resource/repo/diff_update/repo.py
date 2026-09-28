"""repo."""

from dataclasses import dataclass
from pprint import pp
from uuid import UUID

from neomodel import adb

from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.entry.label import LResource
from tanbun.feature.entry.resource.repo.diff_update.cypher import (
    build_varnames,
    delete_term_qs,
    insert_term_q,
    match_nodes,
    match_rel_for_del,
    merge_edge_q,
    update_sentence_q,
)
from tanbun.feature.entry.resource.repo.diff_update.domain.builder import (
    build_sentency_updiff,
    build_term_updiff,
)
from tanbun.feature.entry.resource.repo.diff_update.domain.domain import (
    diff2sets,
    edges2nodes,
    sysnet2edges,
)
from tanbun.feature.entry.resource.repo.restore import restore_sysnet
from tanbun.feature.entry.resource.repo.retirement import (
    retire_or_delete_sentence_qs,
)
from tanbun.feature.entry.resource.repo.save import EdgeRel, q_create_node, t2labels
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.sysnode import KNode, Sentency


@dataclass(frozen=True)
class ResourceDiffPlan:
    """検証済みで、そのままDBへ適用できるResource差分."""

    old: SysNet
    uids: dict[KNode, UUID]
    removed_sentences: set[Sentency]
    added_sentences: set[Sentency]
    updated_sentences: dict[Sentency, Sentency]
    removed_terms: set[Term]
    added_terms: set[Term]
    updated_terms: dict[Term, Term]
    removed_edges: set[EdgeRel]
    added_edges: set[EdgeRel]


async def prepare_resource_diff(
    resource_id: UUIDy,
    upd: SysNet,
    identity_resolutions: dict[str, str | None] | None = None,
    term_identity_resolutions: dict[str, str | None] | None = None,
) -> ResourceDiffPlan:
    """DBを変更せず、同一性競合を含む更新差分を検証して組み立てる."""
    old, uids = await restore_sysnet(resource_id)
    rm_s, add_s, upd_s = build_sentency_updiff(old, upd, identity_resolutions)
    rm_t, add_t, upd_t = build_term_updiff(old, upd, term_identity_resolutions)
    e_rem, e_add = diff2sets(sysnet2edges(old), sysnet2edges(upd))
    return ResourceDiffPlan(
        old=old,
        uids=uids,
        removed_sentences=rm_s,
        added_sentences=add_s,
        updated_sentences=upd_s,
        removed_terms=rm_t,
        added_terms=add_t,
        updated_terms=upd_t,
        removed_edges=e_rem,
        added_edges=e_add,
    )


# 単文のuidをなるべく不変にする
async def update_resource_diff(
    resource_id: UUIDy,
    upd: SysNet,
    do_print: bool = False,  # noqa: FBT001, FBT002
    *,
    identity_resolutions: dict[str, str | None] | None = None,
    term_identity_resolutions: dict[str, str | None] | None = None,
    plan: ResourceDiffPlan | None = None,
):
    """更新差分の反映."""
    plan = plan or await prepare_resource_diff(
        resource_id,
        upd,
        identity_resolutions,
        term_identity_resolutions,
    )
    sn = plan.old
    uids = plan.uids
    rm_s = plan.removed_sentences
    add_s = plan.added_sentences
    upd_s = plan.updated_sentences
    rm_t = plan.removed_terms
    add_t = plan.added_terms
    upd_t = plan.updated_terms
    e_rem = plan.removed_edges
    e_add = plan.added_edges
    varnames = build_varnames(
        sn,
        upd,
        rm_t | set(upd_t.keys()),
        add_t | set(upd_t.values()),
        rm_s | set(upd_s.keys()) | edges2nodes(e_rem),
        add_s | set(upd_s.values()) | edges2nodes(e_add),
    )

    if do_print:
        print("#" * 30)  # noqa: T201
        pp(varnames)
        print(f"{rm_s =}")  # noqa: T201
        print(f"{add_s =}")  # noqa: T201
        print(f"{upd_s =}")  # noqa: T201
        print(f"{rm_t =}")  # noqa: T201
        print(f"{add_t =}")  # noqa: T201
        print(f"{upd_t =}")  # noqa: T201
        print(f"{e_rem =}")  # noqa: T201
        print(f"{e_add =}")  # noqa: T201

    def build_query() -> list[str]:  # local varsを減らす
        q_delrels, var_delrels = match_rel_for_del(e_rem, varnames)
        t_for_del = rm_t | set(upd_t.keys())
        match_t, q_t_del = delete_term_qs(t_for_del, varnames, sn)

        new2old = {v: k for k, v in upd_s.items()}
        qs = [
            f"MATCH (root:{t2labels(LResource)} {{uid: $uid}})",
            *match_nodes(varnames, uids),
            *match_t,
            *q_delrels,
            *q_t_del,
            *retire_or_delete_sentence_qs(rm_s, varnames),
            *[q_create_node(n, varnames) for n in add_s],
            *[merge_edge_q(e, varnames, new2old) for e in e_add],
            *[f"DELETE {r}" for r in var_delrels],
            *[
                insert_term_q(t, varnames, upd, new2old)
                for t in add_t | set(upd_t.values())
            ],
            *[update_sentence_q(o, n, varnames) for o, n in upd_s.items()],
        ]
        return [q for q in qs if q is not None]

    qs = build_query()
    if len(qs) == 1:  # 規定のroot matchだけで更新なし
        return

    query = "\n".join(qs)
    if do_print:
        print("-" * 30)  # noqa: T201
        print(query)  # noqa: T201

    await adb.cypher_query(
        query,
        params={"uid": to_uuid(resource_id).hex},
    )
