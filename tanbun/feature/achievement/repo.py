"""repo."""

from datetime import datetime, timedelta
from typing import get_args

from neomodel.async_.core import AsyncDatabase

from tanbun.feature.domain.datetime import TZ
from tanbun.feature.domain.types import UUIDy, to_uuid
from tanbun.feature.gamification.usecase import fetch_learning_progresses
from tanbun.feature.repo.cypher import Paging
from tanbun.feature.user.public_schema import UserReadPublic

from .cypher import q_archivement
from .domain import (
    AchievementHistories,
    AchievementHistory,
    UserAchievement,
    UserActivities,
    UserActivity,
    UserSearchOrderKey,
    UserSearchResult,
    UserSearchRow,
)


async def fetch_user_with_current_achivement(  # noqa: PLR0914
    search_str: str = "",
    paging: Paging = Paging(),
    keys: list[UserSearchOrderKey] | None = None,
    desc: bool = True,  # noqa: FBT001, FBT002
) -> UserSearchResult:
    """リアルタイムの成果でユーザー検索."""
    if keys is None:
        keys = ["username"]
    skeys = []
    for k in keys:
        if k in get_args(UserSearchOrderKey):
            if k in {"username", "display_name"}:
                skeys.append(f"u.{k}")
            else:
                skeys.append(k)
        else:
            msg = f"Unknown order key: {k}"
            raise TypeError(msg)

    q = f"""
        MATCH (u:User)
        WHERE u.username CONTAINS $s
            OR u.display_name CONTAINS $s
        {q_archivement("u")}
        ORDER BY {", ".join(skeys)} {((desc and "DESC") or "ASC")}
        WITH COLLECT({{
            u:u
            , n_char:n_char
            , n_sentence:n_sentence
            , n_resource:n_resource
        }}) AS results
        RETURN SIZE(results) AS total
            , results[$offset..$offset + $limit] AS page
    """
    rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={
            "s": search_str,
            "offset": paging.skip,
            "limit": paging.size,
        },
    )
    data = []
    if not rows:
        return UserSearchResult(total=0, data=[])
    total, page = rows[0]
    progresses = await fetch_learning_progresses(
        [UserReadPublic.model_validate(row["u"]).id for row in page],
    )
    now = datetime.now(tz=TZ)
    for row in page:
        u, n_char, n_sentence, n_resource = (
            row["u"],
            row["n_char"],
            row["n_sentence"],
            row["n_resource"],
        )
        user = UserReadPublic.model_validate(u)
        archivement = UserAchievement(
            n_char=n_char,
            n_sentence=n_sentence,
            n_resource=n_resource,
            created=now,
        )
        progress = progresses[user.id.hex]
        data.append(
            UserSearchRow(
                user=user,
                archivement=archivement,
                level=progress.level,
            ),
        )
    return UserSearchResult(
        total=total,
        data=data,
    )


def week_start(now: datetime) -> str:
    """現在から6日前の日付を計算し、Cypherパラメータ用の文字列として返す."""
    week_start_dt = now - timedelta(days=6)
    return week_start_dt.isoformat()


# バッチ実行で使用
async def snapshot_archivement(
    now: datetime = datetime.now(tz=TZ),
    paging: Paging = Paging(size=10000),  # user数多すぎる場合の対応を想定
) -> tuple[int, int, int]:
    """ユーザーごとの週次成果スナップショットを追加。.

    n_all_users, n_target, n_saved のタプルを返す
    過去6日間に未記録のユーザーの成果を記録する
    """
    q = f"""
        MATCH (u: User)
        WITH COLLECT(u) AS all_users
        WITH all_users[$offset..$offset + $limit] AS users_in_page
            , SIZE(all_users) AS n_all_users
        WITH users_in_page, n_all_users
            , SIZE(users_in_page) AS n_target
        UNWIND CASE
            WHEN n_target = 0 THEN [NULL]
            ELSE users_in_page
        END AS u
        OPTIONAL MATCH (u)-[r_latest:ARCHEIVE]->(latest:Archievement)
        WITH u, r_latest, latest
            , CASE
                WHEN latest IS NULL THEN true
                WHEN latest.created < datetime($week_start) THEN true
                ELSE false
              END AS should_create
            , n_all_users
            , n_target

        CALL (u, r_latest, latest, should_create) {{
            WITH u, r_latest, latest, should_create
            WHERE should_create
                AND u IS NOT NULL
            {q_archivement("u")}
                , r_latest, latest
                , should_create

            CREATE (u)-[:ARCHEIVE]->(newA:Archievement {{
                n_char: COALESCE(n_char, 0)
                , n_sentence: COALESCE(n_sentence, 0)
                , n_resource: COALESCE(n_resource, 0)
                , created: datetime($now)
            }})
            DELETE r_latest
            FOREACH(i IN CASE WHEN latest IS NOT NULL THEN [1] ELSE [] END |
                CREATE (newA)-[:PREV]->(latest)
            )
            RETURN COUNT(u) AS total_created
        }} IN TRANSACTIONS OF 1 ROW // Neo4j 5.0 以降の並列化オプション
        RETURN n_all_users
            , n_target
            , SUM(total_created) AS n_saved
    """

    rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={
            "offset": paging.skip,
            "limit": paging.size,
            "now": now.isoformat(),
            "week_start": week_start(now),
        },
    )
    return tuple(rows[0])  # total,


async def fetch_achievement_history(
    user_ids: list[UUIDy],
) -> AchievementHistories:
    """直近成果の活動を取得."""
    q = """
        UNWIND $user_ids AS uid
        MATCH (u:User {uid: uid})-[r:ARCHEIVE]->(a:Archievement)
        CALL (a) {
            OPTIONAL MATCH p = (a)-[:PREV]->*(prev:Archievement)
            WITH p, LENGTH(p) as len
            ORDER BY len DESC
            LIMIT 1
            RETURN nodes(p) AS achievements
        }
        RETURN u, achievements
    """

    rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={
            "user_ids": [to_uuid(uid).hex for uid in user_ids],
        },
    )
    hs = []
    for row in rows:
        u, achivements = row
        h = AchievementHistory(
            user=UserReadPublic.model_validate(u),
            archivements=[UserAchievement.model_validate(a) for a in achivements],
        )

        hs.append(h)

    return AchievementHistories(root=hs)


async def fetch_activity(user_ids: list[UUIDy]) -> UserActivities:
    """ID指定で最近の成果と現在成果の差分を取得."""
    q = f"""
        UNWIND $user_ids AS uid
        MATCH (u:User {{uid: uid}})
        {q_archivement("u")}
        OPTIONAL MATCH (u)-[r:ARCHEIVE]->(latest:Archievement)
        RETURN u
            , n_char
            , n_sentence
            , n_resource
            , latest
    """
    rows, _ = await AsyncDatabase().cypher_query(
        q,
        params={
            "user_ids": [to_uuid(uid).hex for uid in user_ids],
        },
    )

    now = datetime.now(tz=TZ)
    data = []
    for row in rows:
        u, n_char, n_sentence, n_resource, latest = row
        user = UserReadPublic.model_validate(u)

        if latest is not None:
            latest = UserAchievement.model_validate(latest)

        a = UserActivity(
            user=user,
            latest=latest,
            current=UserAchievement(
                n_char=n_char,
                n_sentence=n_sentence,
                n_resource=n_resource,
                created=now,
            ),
        )
        data.append(a)
    return UserActivities(root=data)
