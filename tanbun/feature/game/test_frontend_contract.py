"""実フロントの設定PUT・スキルJSON出力をAPIで受け付ける消費者契約."""
# ruff: noqa: PLR2004

import json
import os
import subprocess  # noqa: S404
from pathlib import Path
from shutil import which

import pytest

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .catalog import GameCatalog
from .skill_definitions import parse_skill


def consumer_payloads():
    """生成元・更新方法はfixtures/README.mdを参照。モデルから生成し直さない."""
    return json.loads(
        Path(__file__)
        .with_name("fixtures")
        .joinpath("frontend_game_contract.json")
        .read_text(encoding="utf-8"),
    )


@pytest.mark.skipif(
    not os.environ.get("TANBUNISM_WEB_ROOT"),
    reason="frontend checkout not configured",
)
def test_current_frontend_still_emits_the_captured_contract():
    """隣接checkoutがある環境では、本物のTS変換結果と固定契約を比較する."""
    node = which("node")
    assert node, "Install Node.js before enabling the live frontend contract check"
    # Explicit opt-in; trusted local checkout and fixed script, never shell input.
    result = subprocess.run(  # noqa: S603
        [
            node,
            str(
                Path(__file__).resolve().parents[3]
                / "scripts"
                / "capture_frontend_game_contract.mjs",
            ),
            os.environ["TANBUNISM_WEB_ROOT"],
        ],
        input=json.dumps(consumer_payloads()),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == consumer_payloads()


@mark_async_test()
async def test_frontend_catalog_and_skill_export_survive_http_storage(ac):
    """実際のPUT本文を保存・再読込し、同じフロントの交換ファイルも検査する."""
    user = await aregister("frontend-contract@example.com")
    user.is_superuser = True
    await user.save()
    headers = await aauth_header(user.email)
    payloads = consumer_payloads()
    response = await ac.put(
        "/admin/game/catalog",
        headers=headers,
        json=payloads["catalog"],
    )
    assert response.status_code == 200, response.text
    loaded = await ac.get("/admin/game/catalog", headers=headers)
    assert loaded.status_code == 200, loaded.text
    assert GameCatalog.model_validate(loaded.json()) == GameCatalog.model_validate(
        payloads["catalog"],
    )
    preview = await ac.post(
        "/admin/game/skills/preview",
        headers=headers,
        json={"catalog": loaded.json(), "document": payloads["skill_file"]},
    )
    assert preview.status_code == 200, preview.text
    assert GameCatalog.model_validate(preview.json()) == GameCatalog.model_validate(
        payloads["catalog"],
    )
    # 統合JSON取り込み口でも同じスキル出力を扱える。プレビューでは保存しない。
    unified = await ac.post(
        "/admin/game/settings-file/preview",
        headers=headers,
        json={"document": payloads["skill_file"]},
    )
    assert unified.status_code == 200, unified.text
    assert GameCatalog.model_validate(unified.json()["document"]["catalog"]) == (
        GameCatalog.model_validate(payloads["catalog"])
    )
    assert (
        await ac.get("/admin/game/catalog", headers=headers)
    ).json() == loaded.json()


@pytest.mark.parametrize(
    "effect",
    ["attack", "heal_hp", "buff", "debuff", "ailment", "recover_moves"],
)
def test_consumer_fixture_covers_effect_specific_keys(effect):
    """6種類を網羅し、不要キーを付けた入力を黙って修復しない."""
    catalog = consumer_payloads()["catalog"]
    skill = next(row for row in catalog["skills"] if row["effect"] == effect)
    parse_skill(skill)
    with pytest.raises(ValueError, match="obsolete_field"):
        parse_skill({**skill, "obsolete_field": True})
