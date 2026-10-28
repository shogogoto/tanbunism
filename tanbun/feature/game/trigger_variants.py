"""トリガー固有の値の正規化と、共通セット参照を使った派生の解決."""

from .variant_sets import attached_sets, selected_values

MAX_TRIGGER_VARIANT_DEPTH = 16
MAX_TRIGGER_VARIANT_SETS = 8
TRIGGER_VALUE_FIELDS = {
    "condition",
    "threshold",
    "state_present",
    "state_skill_id",
    "target",
    "stat",
    "stat_basis",
    "comparison",
}


def apply_trigger_patch(base, patch):
    """旧HP/MP条件の意味を保持し、項目変更時に汎用ステータス条件へ揃える."""
    if {"stat", "stat_basis", "comparison"} & patch.keys() and base.get(
        "condition",
        "",
    ).startswith(("hp_", "mp_")):
        basis_selected = "stat_basis" in patch
        resource, comparison = base["condition"].split("_")
        patch = {
            "condition": "stat",
            "stat": resource,
            "stat_basis": "percent",
            "comparison": comparison,
            **patch,
        }
        if patch["stat"] not in {"hp", "mp"} and not basis_selected:
            patch["stat_basis"] = "current"
    return {**base, **patch}


def resolve_trigger_variants(trigger, sets):  # noqa: C901, PLR0915
    """必要な組み合わせだけを解決。既存の単一セットは同じ候補IDで移す."""
    trigger = dict(trigger)
    legacy = trigger.get("variant_set_id")
    if legacy:
        if legacy not in sets:
            msg = "ガンビット変型セットが見つかりません。"
            raise ValueError(msg)
        trigger.update(
            variant_set_ids=[legacy],
            variant_set_id=None,
            variants=[
                {
                    "id": v.id,
                    "name": v.name,
                    "variant_selections": [{"set_id": legacy, "value_id": v.id}],
                }
                for v in sets[legacy].values
            ],
        )
    attached_sets(trigger, sets)
    rows = [dict(row) for row in trigger.get("variants", [])]
    by_id = {row["id"]: row for row in rows}
    if len(by_id) != len(rows):
        msg = "トリガーの変型IDが重複しています。"
        raise ValueError(msg)
    cache, visiting = {}, set()

    def resolve(identity, depth=0):
        if identity in visiting or depth >= MAX_TRIGGER_VARIANT_DEPTH:
            msg = "変型の参照が循環しているか、階層が深すぎます。"
            raise ValueError(msg)
        if identity in cache:
            return cache[identity]
        row = by_id.get(identity)
        if row is None:
            msg = "親の変型が見つかりません。"
            raise ValueError(msg)
        visiting.add(identity)
        parent = (
            resolve(row["base_variant_id"], depth + 1)
            if row.get("base_variant_id")
            else None
        )
        level = parent["_depth"] + 1 if parent else 1
        if level > MAX_TRIGGER_VARIANT_DEPTH:
            msg = "変型の階層が深すぎます。"
            raise ValueError(msg)
        base = parent["resolved_values"] if parent else trigger
        parent_ids = (
            parent["effective_set_ids"]
            if parent
            else trigger.get("variant_set_ids", [])
        )
        own_ids = row.get("variant_set_ids", [])
        if len(own_ids) != len(set(own_ids)):
            msg = "変型セットの参照が重複しています。"
            raise ValueError(msg)
        attached = list(dict.fromkeys([*parent_ids, *own_ids]))
        if len(attached) > MAX_TRIGGER_VARIANT_SETS:
            msg = "変型セットは8個まで設定できます。"
            raise ValueError(msg)
        attached_sets({"variant_set_ids": attached}, sets)
        selections = row.get("variant_selections", [])
        if selections:
            if row.get("value") is not None or row.get("field"):
                msg = "セットの組み合わせと個別の上書きは併用できません。"
                raise ValueError(msg)
            patch, labels = selected_values({"variant_set_ids": parent_ids}, row, sets)
        else:
            field = (
                row.get("field")
                or by_id.get(row.get("base_variant_id"), {}).get("child_field")
                or trigger.get("variant_field", "threshold")
            )
            expected = {"threshold": int, "state_present": bool}.get(field, str)
            if row.get("value") is not None and type(row["value"]) is not expected:
                msg = "トリガーの変型の値が不正です。"
                raise ValueError(msg)
            patch = {field: row["value"]} if row.get("value") is not None else {}
            labels = [row["name"]]
        values = apply_trigger_patch(base, patch)
        result = {
            **row,
            "_depth": level,
            "resolved_values": {
                k: v for k, v in values.items() if k in TRIGGER_VALUE_FIELDS
            },
            "resolved_name": ", ".join([
                *([parent["resolved_name"]] if parent else []),
                *labels,
            ]),
            "effective_set_ids": attached,
        }
        cache[identity] = result
        visiting.remove(identity)
        return result

    trigger["variants"] = [
        {k: v for k, v in resolve(row["id"]).items() if k != "_depth"} for row in rows
    ]
    return trigger
