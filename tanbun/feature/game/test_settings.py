"""管理者の戦闘設定と出題期限の共有."""
# ruff: noqa: PLR2004

from httpx import AsyncClient
from neomodel import adb

from tanbun.conftest import mark_async_test
from tanbun.feature.user.testing import aauth_header, aregister

from .settings import BattleSettings


@mark_async_test()
async def test_battle_settings_permissions_and_validation(ac: AsyncClient):
    """基本秒数と4種の重みは管理者だけ変更でき、範囲外を拒否する."""
    user = await aregister("battle-settings@example.com")
    headers = await aauth_header(user.email)
    path = "/admin/settings/battle"
    defaults = BattleSettings().model_dump()
    assert (await ac.get(path)).status_code == 401
    assert (await ac.put(path, headers=headers, json=defaults)).status_code == 403
    user.is_superuser = True
    await user.save()
    assert (await ac.get(path, headers=headers)).json() == defaults
    rows, _ = await adb.cypher_query("MATCH (s:AdminSettings {key:'battle'}) RETURN s")
    assert rows == []
    changed = defaults | {"base_seconds": 20, "pair2rel": 2}
    assert (await ac.put(path, headers=headers, json=changed)).json() == changed
    assert (await ac.get(path, headers=headers)).json() == changed
    for field, value in (("base_seconds", 0), ("pair2rel", 6), ("rel2pair", -1)):
        assert (
            await ac.put(path, headers=headers, json=changed | {field: value})
        ).status_code == 422


@mark_async_test()
async def test_deadline_is_shared_preserved_and_starts_after_feedback(ac: AsyncClient):
    """サーバー発行の期限は端末変更で延長せず、新問と管理設定を反映する."""
    user = await aregister("battle-deadline@example.com")
    headers = await aauth_header(user.email)
    payload = {
        "revision": 0,
        "save": {
            "version": 2,
            "clears": {},
            "run": {
                "resourceId": "book",
                "name": "本",
                "hp": 35,
                "maxHp": 35,
                "attack": 10,
                "defense": 1,
                "moves": 1,
                "kills": 0,
                "enemyHp": 20,
                "enemyMaxHp": 20,
                "quizCursor": 0,
                "readIds": ["sentence"],
                "phase": "battle",
                "answerDeadline": 9999999999999,
            },
            "content": {"quizzes": [{"quiz_type": "pair2rel"}]},
        },
    }
    path = "/game/state"
    first = (await ac.put(path, headers=headers, json=payload)).json()
    run = first["save"]["run"]
    assert run["answerSeconds"] == 45
    assert run["answerDeadline"] != 9999999999999
    assert (await ac.get(path, headers=headers)).json() == first
    deadline = run["answerDeadline"]
    first["save"]["run"]["answerDeadline"] = 9999999999999
    same = (await ac.put(path, headers=headers, json=first)).json()
    assert same["save"]["run"]["answerDeadline"] == deadline
    same["save"]["run"]["quizCursor"] = 1
    same["save"]["battleFeedback"] = "不正解"
    feedback = (await ac.put(path, headers=headers, json=same)).json()
    assert feedback["save"]["run"]["answerDeadline"] is None
    user.is_superuser = True
    await user.save()
    await ac.put(
        "/admin/settings/battle",
        headers=headers,
        json=BattleSettings(base_seconds=20, pair2rel=2).model_dump(),
    )
    feedback["save"]["battleFeedback"] = None
    next_question = (await ac.put(path, headers=headers, json=feedback)).json()
    assert next_question["save"]["run"]["answerSeconds"] == 40
    assert next_question["save"]["run"]["answerDeadline"] is not None
