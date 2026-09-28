"""cypher文作成."""

from collections.abc import Iterable
from uuid import UUID

from tanbun.feature.domain.types import Duplicable, to_uuid
from tanbun.feature.entry.resource.repo.save import EdgeRel, rel2q
from tanbun.feature.parsing.primitive.term import Term
from tanbun.feature.parsing.primitive.time import WhenNode
from tanbun.feature.parsing.sysnet import SysNet
from tanbun.feature.parsing.sysnet.sysnode import Def, KNode, Sentency


def n2label(n: KNode) -> str:
    """nodeのラベル."""
    match n:
        case WhenNode():
            return "Interval"
        case str() | Duplicable():
            return "Sentence"
        case Term():
            return "Term"
        case _:
            raise TypeError(n)


def match_nodes(varnames: dict[KNode, str], uids: dict[KNode, UUID]) -> list[str]:
    """差分更新のために既存ノードをマッチさせる."""
    qs = []
    for n, name in varnames.items():
        if name == "root":
            continue
        uid = uids.get(n)
        if uid is not None:
            q = f"MATCH ({name}:{n2label(n)} {{uid: '{to_uuid(uid).hex}'}})"
            qs.append(q)
    return qs


# uid特定はmatch_qsで済んでいる想定
def match_rel_for_del(
    rels: Iterable[EdgeRel],
    varnames: dict[KNode, str],
) -> tuple[list[str], list[str]]:
    """関係を削除."""
    qs = []
    vars_rel = []
    for i, rel in enumerate(rels):
        u, v, t = rel
        var_u = varnames[u]
        var_v = varnames[v]
        var_rel = f"rel{i}"
        q = f"MATCH ({var_u}) -[{var_rel}:{t.arrow}]-> ({var_v})"
        vars_rel.append(var_rel)
        qs.append(q)
    return qs, vars_rel


def delete_term_qs(
    terms: Iterable[Term],
    varnames: dict[KNode, str],
    sn: SysNet,
) -> tuple[list[str], list[str]]:
    """termの更新."""
    q_rels = []
    q_dels = []
    for t in terms:
        df = sn.get(t)
        if not isinstance(df, Def):
            raise TypeError
        var = varnames[df.sentence]
        q = f"MATCH (term_{var}:Term)-[:DEF|ALIAS]-*({var})"
        d = f"DETACH DELETE term_{var}"
        q_rels.append(q)
        q_dels.append(d)
    return q_rels, q_dels


def insert_term_q(
    term: Term,
    varnames: dict[KNode, str],
    sn: SysNet,
    new2old_sent: dict[Sentency, Sentency],
) -> str:
    """termの登録."""
    df = sn.get(term)
    if not isinstance(df, Def):
        raise TypeError
    s = new2old_sent.get(df.sentence, df.sentence)
    var = varnames[s]
    q = f"CREATE ({var})<-[:DEF]-(:Term {{val: '{term.names[0]}'}})"
    for name in term.names[1:]:
        q += f"-[:ALIAS]->(:Term {{val: '{name}'}})"
    return q


def update_sentence_q(
    old: Sentency,
    new: Sentency,
    varnames: dict[KNode, str],
) -> str:
    """既存uidを保持しつつ単文を更新 or 新規登録."""
    var = varnames[old]

    # if var is None:  # 新規登録
    #     q = f"CREATE ({var}:Sentence {{val: '{new}'}})"
    # else:  # 既存更新
    #     q = f"SET {var}.val = '{new}'"
    return f"SET {var}.val = '{new}'"


def merge_edge_q(
    e: EdgeRel,
    varnames: dict[KNode, str],
    new2old_sent: dict[Sentency, Sentency],
):
    """関係更新."""
    u, v, t = e
    u2 = new2old_sent.get(u, u)
    v2 = new2old_sent.get(v, v)

    return rel2q((u2, v2, t), varnames)


def build_varnames(  # noqa: PLR0917
    old: SysNet,
    upd: SysNet,
    old_t: set[Term],
    upd_t: set[Term],
    old_s: set[Sentency],
    upd_s: set[Sentency],
) -> dict[KNode, str]:
    """差分更新のための変数名の作成."""
    upd_defs = {old.get(t) for t in old_t} | {upd.get(t) for t in upd_t}
    sentences = (
        old_s
        | upd_s
        | {d.sentence for d in upd_defs if isinstance(d, Def)} - {old.root}
    )
    return {sent: f"n{i}" for i, sent in enumerate(sentences)} | {old.root: "root"}
