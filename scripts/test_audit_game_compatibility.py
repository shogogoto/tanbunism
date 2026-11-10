"""本番へ接続せず、件数分類・出力秘匿・rollbackを検証する."""
# ruff: noqa: PLR2004

import json
from collections import Counter
from unittest.mock import MagicMock, call

import pytest
from neo4j import READ_ACCESS, GraphDatabase
from neo4j.exceptions import ServiceUnavailable

from scripts import audit_game_compatibility as audit


def counts():
    """表示するキーを固定した空の集計."""
    return Counter(dict.fromkeys(audit.METRICS, 0))


def test_snapshot_counts_distinguish_old_and_current_equipment():
    """現在の装備キャッシュが単一装備用フィールドを持っても旧形式としない."""
    result = counts()
    audit.audit_snapshot(None, result)
    legacy = {
        "save": {
            "version": 2,
            "battle": {
                "schedule": None,
                "gambitAction": {"skill": {"effect": "attack", "ailment": "poison"}},
            },
            "economy": {"weapon": "sword", "inventory": {"sword": 1}},
            "gambits": [{"condition": "poisoned"}],
        },
    }
    current = {
        "save": {
            "version": 2,
            "battle": {"schedule": {"actor_id": "player"}},
            "economy": {
                "weapon": "sword",
                "inventory": {"sword": 1},
                "equipment": {"arm1": {"item_id": "sword"}},
            },
        },
    }
    audit.audit_snapshot(json.dumps(legacy), result)
    audit.audit_snapshot(json.dumps(current), result)
    assert result["users_scanned"] == 3
    assert result["users_without_snapshot"] == 1
    assert result["non_ctb_active_battles"] == 1
    assert result["ctb_active_battles"] == 1
    assert result["legacy_equipped_users"] == 1
    assert result["legacy_equipment_fallback_users"] == 1
    assert result["snapshot_obsolete_effect_objects"] == 1
    assert result["snapshot_retired_trigger_objects"] == 1


@pytest.mark.parametrize("raw", ["not-json", "[]", "null", 9])
def test_invalid_json_is_counted_without_echoing_it(raw):
    """不明な保存を黙って正常データとして数えない."""
    result = counts()
    audit.audit_snapshot(raw, result)
    assert result["unreadable_snapshots"] == 1
    assert result["users_with_snapshot"] == 1


@pytest.mark.parametrize("save", [{"version": 1}, {}, {"battle": []}, {"economy": []}])
def test_unknown_shapes_and_versions_are_visible(save):
    """推測で旧セーブを補正せず、不明点を件数に残す."""
    result = counts()
    audit.audit_snapshot(json.dumps({"save": save}), result)
    assert (
        result["unknown_structures"]
        or result["unsupported_save_versions"]
        or result["missing_save_versions"]
    )


def test_settings_count_flags_without_migrating_or_using_app_models():
    """旧補正・利用フラグ・無関係な効果項目を、それぞれ数える."""
    result = counts()
    audit.audit_setting("game-balance", '{"boss_hp_multiplier":3}', result)
    document = {
        "skills": [{"effect": "attack", "castable": True, "ailment": "poison"}],
        "skill_templates": [{"mp_cost": 3}],
        "triggers": [{"condition": "state"}],
    }
    audit.audit_setting("game_catalog", json.dumps(document), result)
    assert result["legacy_boss_balance_keys"] == 1
    assert result["catalogs_missing_boss_version"] == 1
    assert result["catalog_old_usage_flags"] == 1
    assert result["catalog_old_template_costs"] == 1
    assert result["catalog_obsolete_effect_objects"] == 1
    assert result["catalog_retired_trigger_objects"] == 1
    assert "boss_multipliers_version" not in document


def test_inactive_old_gear_and_battle_without_marker_are_not_missed():
    """現在は効果がなくても旧保存が残る場合と、単体戦闘の保存を数える."""
    result = counts()
    audit.audit_snapshot(
        json.dumps({
            "save": {
                "version": 2,
                "run": {"phase": "battle"},
                "economy": {"weapon": "sword", "inventory": {"sword": 0}},
            },
        }),
        result,
    )
    assert result["battle_phase_without_marker"] == 1
    assert result["legacy_equipment_users"] == 1
    assert result["legacy_equipped_users"] == 0


def test_unknown_equipment_structure_is_counted():
    """不明な装備形式を旧装備0件の根拠にしない."""
    result = counts()
    audit.audit_equipment({"equipment": []}, result)
    assert result["unknown_structures"] == 1


def test_connection_strips_userinfo_and_checks_the_host():
    """DBのURIをログへ渡さず、接続先の取り違えも防ぐ."""
    env = {"NEO4J_URL": "bolt://audit:p%40ss@db.example:7687"}
    assert audit.connection(env, "db.example") == (
        "bolt://db.example:7687",
        ("audit", "p@ss"),
    )
    with pytest.raises(ValueError, match="ホスト"):
        audit.connection(env, "localhost")


def fake_driver():
    """ドライバーだけを置換し、固定queryとrollbackを実処理で検証する."""
    driver = MagicMock()
    driver.__enter__.return_value = driver
    session = driver.session.return_value.__enter__.return_value
    tx = session.begin_transaction.return_value
    tx.run.side_effect = [[], []]
    return driver, session, tx


def test_only_two_read_queries_are_issued_and_success_rolls_back():
    """READ_ACCESSだけを安全境界とせず、queryを固定しcommitを呼ばない."""
    driver, session, tx = fake_driver()
    assert audit.read_counts(driver, "neo4j") == counts()
    driver.session.assert_called_once_with(
        database="neo4j",
        default_access_mode=READ_ACCESS,
        fetch_size=25,
    )
    session.begin_transaction.assert_called_once_with(timeout=30)
    assert tx.run.call_args_list == [call(audit.USER_QUERY), call(audit.SETTINGS_QUERY)]
    tx.rollback.assert_called_once_with()
    tx.commit.assert_not_called()


def test_query_failure_also_rolls_back():
    """取得途中で失敗してもcommitしない."""
    driver, _, tx = fake_driver()
    tx.run.side_effect = ServiceUnavailable("synthetic connection failure")
    with pytest.raises(ServiceUnavailable):
        audit.read_counts(driver, "neo4j")
    tx.rollback.assert_called_once_with()
    tx.commit.assert_not_called()


def test_main_never_prints_saved_contents_or_identifiers(monkeypatch, capsys):
    """出力は固定キーの件数だけで、保存された名前・メール・本文を含めない."""
    driver, _, tx = fake_driver()
    tx.run.side_effect = [
        [{"document": '{"save":{"version":2,"content":{"private":"PRIVATE_NOTES"}}}'}],
        [],
    ]
    monkeypatch.setenv("NEO4J_URL", "bolt://audit:dummy@db.example:7687")
    monkeypatch.setattr(GraphDatabase, "driver", lambda *_args, **_kwargs: driver)
    assert audit.main(["--expect-host", "db.example"]) == 0
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["counts"]["users_scanned"] == 1
    assert report["complete"] is True
    assert "PRIVATE_NOTES" not in output.out + output.err
    assert "db.example" not in output.out + output.err
    tx.rollback.assert_called_once_with()


def test_main_hides_connection_errors(monkeypatch, capsys):
    """例外にURIや機密情報が含まれていても表示しない."""
    driver, _, tx = fake_driver()
    tx.run.side_effect = ServiceUnavailable("PRIVATE_CONNECTION")
    monkeypatch.setenv("NEO4J_URL", "bolt://audit:dummy@db.example:7687")
    monkeypatch.setattr(GraphDatabase, "driver", lambda *_args, **_kwargs: driver)
    assert audit.main(["--expect-host", "db.example"]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "PRIVATE_CONNECTION" not in output.err
    tx.rollback.assert_called_once_with()


def test_main_reports_incomplete_scan_instead_of_silent_zeroes(monkeypatch, capsys):
    """不明なセーブを含む場合は、成功終了や互換不要の結論にしない."""
    driver, _, tx = fake_driver()
    tx.run.side_effect = [[{"document": '{"save":{}}'}], []]
    monkeypatch.setenv("NEO4J_URL", "bolt://audit:dummy@db.example:7687")
    monkeypatch.setattr(GraphDatabase, "driver", lambda *_args, **_kwargs: driver)
    assert audit.main(["--expect-host", "db.example"]) == 1
    assert json.loads(capsys.readouterr().out)["complete"] is False
