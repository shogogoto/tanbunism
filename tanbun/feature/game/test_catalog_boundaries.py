"""カタログの分離境界と、既存の公開契約を検証する."""

import subprocess  # noqa: S404 - 新しいPythonプロセスでimport境界を検証する
import sys

import pytest

from . import catalog, gambit_definitions, skill_definitions, skill_templates


@pytest.mark.parametrize(
    "module_name",
    [
        "catalog_identifiers",
        "skill_definitions",
        "skill_templates",
        "skill_outcomes",
        "effects",
        "gambit_definitions",
        "gambit_access",
    ],
)
def test_standalone_definitions_do_not_import_catalog_or_database(module_name):
    """初期化済みのpytestプロセスに依存せず、DBなしで単体ロジックを使える."""
    module = f"tanbun.feature.game.{module_name}"
    code = (
        f"import importlib, sys; importlib.import_module({module!r}); "
        "assert 'tanbun.feature.game.catalog' not in sys.modules; "
        "assert 'neomodel' not in sys.modules"
    )
    result = subprocess.run(  # noqa: S603 - 入力は上記の固定されたモジュール名のみ
        [
            sys.executable,
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "module",
    [skill_definitions, skill_templates, gambit_definitions],
)
def test_catalog_reexports_the_original_class_and_function_objects(module):
    """窓口を維持し、クラスを複製してisinstanceやPydanticの型を変えない."""
    own_objects = {
        name: value
        for name, value in vars(module).items()
        if not name.startswith("_")
        and getattr(value, "__module__", None) == module.__name__
    }
    assert own_objects
    for name, value in own_objects.items():
        assert getattr(catalog, name) is value


def test_catalog_storage_round_trip_keeps_resolved_skill_types():
    """分離後も既存の初期値・保存形式から、同じ効果型として復元する."""
    original = catalog.default_catalog()
    document = original.stored_document()
    restored = catalog.GameCatalog.model_validate(document)
    assert restored.stored_document() == document
    assert restored.model_dump(mode="json") == original.model_dump(mode="json")
    assert isinstance(restored.skills[0], skill_definitions.AttackSkill)
    assert isinstance(restored.skill_templates[0], skill_templates.AttackTemplate)


def test_catalog_models_and_defaults_do_not_load_database_dependencies():
    """定義の検証・JSON化だけなら、DB設定や保存処理を読み込まない."""
    code = (
        "import sys; from tanbun.feature.game.catalog import default_catalog; "
        "catalog = default_catalog(); catalog.stored_document(); "
        "catalog.model_json_schema(); "
        "assert 'neomodel' not in sys.modules; "
        "assert 'tanbun.feature.game.catalog_repo' not in sys.modules; "
        "assert 'tanbun.feature.game.boss_settings_migration' not in sys.modules"
    )
    result = subprocess.run(  # noqa: S603 - 固定コードを同じPythonで検証する
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
