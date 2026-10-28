"""プレイヤーデータを含めない、ゲーム管理設定の一括交換."""

import hashlib
import json
import operator
from typing import Any, Literal

from neomodel import adb
from pydantic import BaseModel, ConfigDict, model_validator

from tanbun.config.transactions import atomic

from .balance import GameBalance, get_game_balance, update_game_balance
from .catalog import GameCatalog, get_game_catalog, update_game_catalog
from .settings import BattleSettings, get_battle_settings, update_battle_settings
from .skill_files import SkillFile, SkillFilePreview


class GameSettingsFile(BaseModel):
    """省略した設定は維持する。リストの定義は安定したIDで更新する."""

    model_config = ConfigDict(extra="forbid")
    format: Literal["tanbunism.game-settings"]
    version: Literal[1]
    catalog: dict[str, Any] | None = None
    balance: dict[str, Any] | None = None
    battle: dict[str, Any] | None = None

    @model_validator(mode="after")
    def allowed_sections(self):
        """未知の項目やnullを黙って捨てず、誤った取り込みを拒否する."""
        sections = {
            "catalog": GameCatalog,
            "balance": GameBalance,
            "battle": BattleSettings,
        }
        for key, model in sections.items():
            value = getattr(self, key)
            if key in self.model_fields_set and value is None:
                msg = f"{key}にnullは指定できません。"
                raise ValueError(msg)
            if value is not None and value.keys() - model.model_fields.keys():
                msg = f"{key}に未対応の項目があります。"
                raise ValueError(msg)
        if not any(getattr(self, key) for key in sections):
            msg = "取り込むゲーム設定がありません。"
            raise ValueError(msg)
        return self


class SettingsSnapshot(BaseModel):
    """管理画面で編集する三つの設定だけをまとめる."""

    catalog: GameCatalog
    balance: GameBalance
    battle: BattleSettings

    def document(self) -> dict:
        """派生値は複製せず、保存用の定義と参照を出力する."""
        return {
            "format": "tanbunism.game-settings",
            "version": 1,
            "catalog": self.catalog.stored_document(),
            "balance": self.balance.model_dump(mode="json"),
            "battle": self.battle.model_dump(mode="json"),
        }

    def revision(self) -> str:
        """プレビュー後の変更を検出する。ファイル内の任意revisionは信用しない."""
        return hashlib.sha256(
            json.dumps(self.document(), sort_keys=True).encode(),
        ).hexdigest()


def entry_key(section, entry):
    """ショップや習得設定も、表示名でなく参照IDで照合する."""
    if not isinstance(entry, dict):
        msg = f"{section}の定義はオブジェクトで指定してください。"
        raise ValueError(msg)  # noqa: TRY004
    if section == "shop":
        key = (
            entry.get("item_id"),
            entry.get("trigger_id"),
            entry.get("trigger_variant_id"),
        )
        if any(part is not None and not isinstance(part, str) for part in key):
            msg = "ショップの参照IDは文字列で指定してください。"
            raise ValueError(msg)
        return key
    key = (
        entry.get("skill_id")
        if section in {"player_skills", "specialty_skills"}
        else entry.get("id")
    )
    if not isinstance(key, str):
        msg = f"{section}のIDは文字列で指定してください。"
        raise ValueError(msg)  # noqa: TRY004
    return key


def merge_entries(section, current, incoming):
    """ファイルの並び順を反映し、記載されていない既存定義は末尾に残す."""
    if not isinstance(incoming, list):
        msg = f"{section}は配列で指定してください。"
        raise ValueError(msg)  # noqa: TRY004
    keys = [entry_key(section, entry) for entry in incoming]
    if len(set(keys)) != len(keys) or any(
        key is None or key == (None, None, None) for key in keys
    ):
        msg = f"{section}のIDが重複しているか、未指定です。"
        raise ValueError(msg)
    included = set(keys)
    return [
        *incoming,
        *(entry for entry in current if entry_key(section, entry) not in included),
    ]


def merge_settings(current: SettingsSnapshot, document: dict) -> SettingsSnapshot:
    """全セクションを統合した後、一度に参照・値を検証する。保存はしない."""
    if document.get("format") == "tanbunism.skills":
        catalog = SkillFilePreview(
            catalog=current.catalog,
            document=SkillFile.model_validate(document),
        ).merged_catalog()
        return current.model_copy(update={"catalog": catalog})
    patch = GameSettingsFile.model_validate(document)
    catalog = current.catalog.stored_document()
    for section, value in (patch.catalog or {}).items():
        catalog[section] = (
            merge_entries(section, catalog.get(section, []), value)
            if isinstance(catalog.get(section, []), list)
            else value
        )
    return SettingsSnapshot(
        catalog=GameCatalog.model_validate(catalog),
        balance=GameBalance.model_validate(
            current.balance.model_dump() | (patch.balance or {}),
        ),
        battle=BattleSettings.model_validate(
            current.battle.model_dump() | (patch.battle or {}),
        ),
    )


async def read_settings() -> SettingsSnapshot:
    """既存の保存境界を通し、旧設定の読み取りと同じ補完を適用する."""
    return SettingsSnapshot(
        catalog=await get_game_catalog(),
        balance=await get_game_balance(),
        battle=await get_battle_settings(),
    )


def settings_preview(current: SettingsSnapshot, document: dict) -> dict:
    """更新前後の差分と、変更前の全設定のバックアップを返す."""
    before = current.document()
    after = merge_settings(current, document).document()
    changes = []
    for section in ("catalog", "balance", "battle"):
        for key in before[section].keys() | after[section].keys():
            old, new = before[section].get(key), after[section].get(key)
            if old != new:
                changes.append({
                    "section": f"{section}.{key}",
                    "before": old,
                    "after": new,
                })
    return {
        "revision": current.revision(),
        "backup": before,
        "document": after,
        "changes": sorted(changes, key=operator.itemgetter("section")),
    }


class SettingsFileRequest(BaseModel):
    """外部ファイルの内容。管理設定以外はモデル境界で拒否する."""

    model_config = ConfigDict(extra="forbid")
    document: dict


class SettingsFileSave(SettingsFileRequest):
    """確認したDB状態と一致するときだけ一括反映する."""

    revision: str


class SettingsChangedError(ValueError):
    """確認中に別の管理者が設定を更新した."""


async def save_settings_file(request: SettingsFileSave) -> dict:
    """設定ノードをロックし、カタログ・数式・時間を同一transactionで保存する."""
    async with atomic():
        await adb.cypher_query(
            "MATCH (s:AdminSettings) WHERE s.key IN $keys "
            "WITH s ORDER BY s.key SET s.settings_file_lock=true",
            {"keys": ["game_catalog", "game-balance", "battle"]},
        )
        current = await read_settings()
        if request.revision != current.revision():
            msg = "設定が変更されています。JSONを再度取り込み、差分を確認してください。"
            raise SettingsChangedError(msg)
        merged = merge_settings(current, request.document)
        await update_game_catalog(merged.catalog)
        await update_game_balance(merged.balance)
        await update_battle_settings(merged.battle)
        await adb.cypher_query(
            "MATCH (s:AdminSettings) WHERE s.key IN $keys REMOVE s.settings_file_lock",
            {"keys": ["game_catalog", "game-balance", "battle"]},
        )
        return merged.document()
