"""CLIローカル設定のテスト."""

from tanbun.config import LocalConfig


def test_none_anchor_is_serialized_as_json_null() -> None:
    """未設定の同期元を文字列Noneへ変換しない."""
    assert '"ANCHOR":null' in LocalConfig().model_dump_json()


def test_upload_history_round_trip() -> None:
    """未変更判定用の履歴を設定JSONから復元できる."""
    config = LocalConfig(
        UPLOAD_HISTORY={"notes::memo.tb": (10, 20)},
    )

    restored = LocalConfig.model_validate_json(config.model_dump_json())

    assert restored.UPLOAD_HISTORY["notes::memo.tb"] == (10, 20)
