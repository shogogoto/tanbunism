"""一回限りのゲーム保存形式の棚卸し。生JSONを読み、件数だけ出力する."""

import argparse
import json
import os
import sys
from collections import Counter
from urllib.parse import unquote, urlsplit, urlunsplit

CURRENT_SAVE_VERSION = 2
USER_QUERY = "MATCH (u:User) RETURN u.game_state AS document"
SETTINGS_QUERY = (
    "MATCH (s:AdminSettings) WHERE s.key IN ['game_catalog', 'game-balance'] "
    "RETURN s.key AS key, s.config AS document"
)
METRICS = (
    "users_scanned",
    "users_without_snapshot",
    "users_with_snapshot",
    "unreadable_snapshots",
    "unsupported_save_versions",
    "missing_save_versions",
    "non_ctb_active_battles",
    "ctb_active_battles",
    "battle_phase_without_marker",
    "legacy_equipment_users",
    "legacy_equipped_users",
    "legacy_equipment_fallback_users",
    "snapshot_obsolete_effect_objects",
    "snapshot_retired_trigger_objects",
    "settings_scanned",
    "unreadable_settings",
    "catalogs_missing_boss_version",
    "legacy_boss_balance_keys",
    "catalog_obsolete_effect_objects",
    "catalog_retired_trigger_objects",
    "catalog_old_usage_flags",
    "catalog_old_template_costs",
    "unknown_structures",
)
LEGACY_FIELDS = {
    "multiplier",
    "amount_mode",
    "stat",
    "duration_turns",
    "stack_limit",
    "ailment",
    "affected_element",
}
RELEVANT_FIELDS = {
    "attack": {"amount_mode", "multiplier"},
    "heal_hp": {"amount_mode"},
    "heal_mp": {"amount_mode"},
    "damage_mp": {"amount_mode"},
    "buff": {
        "amount_mode",
        "stat",
        "duration_turns",
        "stack_limit",
        "affected_element",
    },
    "debuff": {
        "amount_mode",
        "stat",
        "duration_turns",
        "stack_limit",
        "affected_element",
    },
    "ailment": {"amount_mode", "duration_turns", "stack_limit", "ailment"},
    "recover_moves": set(),
    "equip_attack": set(),
}
CONDITIONS = {
    "always",
    "hp_below",
    "hp_above",
    "hp_equal",
    "mp_below",
    "mp_above",
    "mp_equal",
    "enemy_count",
    "state",
    "stat",
}
USAGE_FLAGS = {
    "castable",
    "requires_specialty",
    "quiz_required",
    "quiz_seconds",
    "wrong_multiplier",
    "power_coefficient",
}
BOSS_KEYS = {
    f"boss_{stat}_multiplier"
    for stat in (
        "hp",
        "mp",
        "attack",
        "defense",
        "special_attack",
        "special_defense",
        "speed",
    )
}


def parse_document(raw, counts, error_key):
    """エラー時もJSON本文・識別子・例外の内容は出力しない."""
    try:
        value = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        counts[error_key] += 1
        return None
    if not isinstance(value, dict):
        counts[error_key] += 1
        return None
    return value


def count_nested_compatibility(document, counts, prefix):
    """保存内の効果・条件を変換せず検査する。数値はオブジェクト数."""
    pending = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            effect = value.get("effect")
            if (
                isinstance(effect, str)
                and effect in RELEVANT_FIELDS
                and (LEGACY_FIELDS - RELEVANT_FIELDS[effect]) & value.keys()
            ):
                counts[f"{prefix}_obsolete_effect_objects"] += 1
            condition = value.get("condition")
            if "condition" in value and (
                not isinstance(condition, str)
                or condition not in CONDITIONS
                or (condition == "state" and not value.get("state_skill_id"))
            ):
                counts[f"{prefix}_retired_trigger_objects"] += 1
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def audit_snapshot(raw, counts):
    """プレイヤーを識別せず、進行中戦闘と装備の保存方式を数える."""
    counts["users_scanned"] += 1
    if raw is None or (isinstance(raw, str) and not raw):
        counts["users_without_snapshot"] += 1
        return
    counts["users_with_snapshot"] += 1
    document = parse_document(raw, counts, "unreadable_snapshots")
    if document is None:
        return
    save = document.get("save")
    if not isinstance(save, dict):
        counts["unknown_structures"] += 1
        return
    if "version" not in save:
        counts["missing_save_versions"] += 1
    elif type(save["version"]) is not int or save["version"] != CURRENT_SAVE_VERSION:
        counts["unsupported_save_versions"] += 1
    audit_battle(save.get("battle"), counts)
    if (
        save.get("battle") is None
        and isinstance(save.get("run"), dict)
        and save["run"].get("phase") == "battle"
    ):
        counts["battle_phase_without_marker"] += 1
    audit_equipment(save.get("economy", {}), counts)
    count_nested_compatibility(save, counts, "snapshot")


def audit_battle(battle, counts):
    """保存中の戦闘方式を数える。不明な構造を正常扱いしない."""
    if isinstance(battle, dict):
        schedule = battle.get("schedule")
        if schedule is None:
            counts["non_ctb_active_battles"] += 1
        elif isinstance(schedule, dict):
            counts["ctb_active_battles"] += 1
        else:
            counts["unknown_structures"] += 1
    elif battle is not None:
        counts["unknown_structures"] += 1


def audit_equipment(economy, counts):
    """部位別装備がまだない単一装備を、現在の所持数が0でも数える."""
    if not isinstance(economy, dict) or not isinstance(
        economy.get("inventory", {}),
        dict,
    ):
        counts["unknown_structures"] += 1
    else:
        weapon = economy.get("weapon")
        amount = (
            economy.get("inventory", {}).get(weapon, 0)
            if isinstance(weapon, str)
            else 0
        )
        if weapon is not None and not isinstance(weapon, str):
            counts["unknown_structures"] += 1
        equipment = economy.get("equipment")
        if equipment is not None and not isinstance(equipment, dict):
            counts["unknown_structures"] += 1
        if isinstance(weapon, str) and weapon and equipment is None:
            counts["legacy_equipment_users"] += 1
            counts["legacy_equipped_users"] += type(amount) is int and amount > 0
            if economy.get("equipment_attack") is None:
                counts["legacy_equipment_fallback_users"] += 1


def audit_setting(key, raw, counts):
    """設定もモデルの復元や移行を通さず、旧項目の存在だけを数える."""
    counts["settings_scanned"] += 1
    document = parse_document(raw, counts, "unreadable_settings")
    if document is None:
        return
    if key == "game-balance":
        counts["legacy_boss_balance_keys"] += len(BOSS_KEYS & document.keys())
    elif key == "game_catalog":
        if document.get("boss_multipliers_version") != 1:
            counts["catalogs_missing_boss_version"] += 1
        for field, metric, old_fields in (
            ("skills", "catalog_old_usage_flags", USAGE_FLAGS),
            ("skill_templates", "catalog_old_template_costs", {"mp_cost"}),
        ):
            rows = document.get(field, [])
            if not isinstance(rows, list):
                counts["unknown_structures"] += 1
                continue
            for row in rows:
                if isinstance(row, dict):
                    counts[metric] += bool(old_fields & row.keys())
                else:
                    counts["unknown_structures"] += 1
        count_nested_compatibility(document, counts, "catalog")


def scan_transaction(tx):
    """固定した2本のMATCH/RETURNで保存JSONを読み、ユーザーIDやメールは要求しない."""
    counts = Counter(dict.fromkeys(METRICS, 0))
    for row in tx.run(USER_QUERY):
        audit_snapshot(row["document"], counts)
    for row in tx.run(SETTINGS_QUERY):
        audit_setting(row["key"], row["document"], counts)
    return dict(counts)


def connection(env, expected_host):
    """接続先の取り違えを防ぎ、認証情報をURIから分離する."""
    uri = urlsplit(env.get("NEO4J_URL", ""))
    if not uri.hostname or uri.hostname != expected_host:
        msg = "NEO4J_URLのホストが確認済みの対象と一致しません。"
        raise ValueError(msg)
    user = unquote(uri.username) if uri.username is not None else env.get("NEO4J_USER")
    password = (
        unquote(uri.password) if uri.password is not None else env.get("NEO4J_PASSWORD")
    )
    if (user is None) != (password is None):
        msg = "DBの認証設定が不完全です。"
        raise ValueError(msg)
    address = urlunsplit((
        uri.scheme,
        uri.netloc.rsplit("@", 1)[-1],
        uri.path,
        uri.query,
        uri.fragment,
    ))
    return address, (user, password) if user is not None else None


def read_counts(driver, database):
    """読取ルーティングを指定し、正常終了でもcommitせずrollbackする."""
    from neo4j import READ_ACCESS  # noqa: PLC0415 - helpでDB依存をロードしない

    with driver.session(
        database=database,
        default_access_mode=READ_ACCESS,
        fetch_size=25,
    ) as session:
        tx = session.begin_transaction(timeout=30)
        try:
            return scan_transaction(tx)
        finally:
            tx.rollback()


def main(argv=None):
    """アプリを起動せず、環境変数に設定されたDBの件数のみ表示する."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-host",
        required=True,
        help="事前に確認したDBホスト名(必須)",
    )
    parser.add_argument("--database", default=os.environ.get("NEO4J_DATABASE", "neo4j"))
    args = parser.parse_args(argv)
    from neo4j import GraphDatabase  # noqa: PLC0415 - 実行時だけDBドライバーを読む
    from neo4j.exceptions import DriverError, Neo4jError  # noqa: PLC0415

    try:
        address, auth = connection(os.environ, args.expect_host)
        with GraphDatabase.driver(address, auth=auth, connection_timeout=10) as driver:
            counts = read_counts(driver, args.database)
    except (ValueError, OSError, DriverError, Neo4jError):
        print(  # noqa: T201 - 単独CLIのエラー表示は機密情報を含めない
            "集計できませんでした。接続先・認証・読取権限を確認してください。",
            file=sys.stderr,
        )
        return 2
    complete = not any(
        counts[key]
        for key in (
            "unreadable_snapshots",
            "unreadable_settings",
            "unknown_structures",
            "unsupported_save_versions",
            "missing_save_versions",
        )
    )
    print(  # noqa: T201 - 単独CLIの出力は件数のみ
        json.dumps(
            {
                "format": "tanbunism.game-compatibility-audit",
                "complete": complete,
                "counts": counts,
            },
            indent=2,
        ),
    )
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
