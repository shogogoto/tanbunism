"""効果・トリガーで共通の、候補IDとセット参照の組み合わせ."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Identity = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class VariantCandidate(BaseModel):
    """副名を変更しても変わらない候補の識別子."""

    model_config = ConfigDict(extra="forbid")
    id: Identity
    name: str = Field(min_length=1, max_length=60)


class VariantSelection(BaseModel):
    """値を複製せず、使う候補を参照する."""

    model_config = ConfigDict(extra="forbid")
    set_id: Identity
    value_id: Identity


def attached_sets(template, sets):
    """同じ項目の重複を禁止し、上書き順に結果を依存させない."""
    ids = template.get("variant_set_ids", [])
    if len(ids) != len(set(ids)) or any(identity not in sets for identity in ids):
        msg = "変型セットの参照が重複しているか、見つかりません。"
        raise ValueError(msg)
    selected = [sets[identity] for identity in ids]
    if len({s.field for s in selected}) != len(selected):
        msg = "同じ項目を変更する変型セットは併用できません。"
        raise ValueError(msg)
    return selected


def selected_values(base, template, sets):
    """親で指定した順に、選択した候補の値と副名をまとめる."""
    attached = attached_sets(base, sets)
    selections = [
        VariantSelection.model_validate(s)
        for s in template.get("variant_selections", [])
    ]
    by_id = {s.set_id: s.value_id for s in selections}
    if len(by_id) != len(selections) or set(by_id) - {s.id for s in attached}:
        msg = (
            "組み合わせの変型セットが重複しているか、テンプレートに設定されていません。"
        )
        raise ValueError(msg)
    values, labels = {}, []
    for shared in attached:
        if shared.id not in by_id:
            continue
        candidate = next((v for v in shared.values if v.id == by_id[shared.id]), None)
        if candidate is None:
            msg = "変型セットの候補が見つかりません。"
            raise ValueError(msg)
        values[shared.field] = candidate.value
        labels.append(candidate.name)
    return values, labels
