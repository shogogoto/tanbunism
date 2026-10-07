"""単文router test."""

from datetime import datetime
from uuid import UUID

import pytz
from fastapi import status
from fastapi.testclient import TestClient
from neomodel import adb

from tanbun.api import root_router
from tanbun.conftest import async_fixture, mark_async_test
from tanbun.feature.entry.resource.usecase import save_text
from tanbun.feature.repo.cypher import Paging
from tanbun.feature.tanbun.domain import TanbunChains
from tanbun.feature.tanbun.label import LSentence, LTerm
from tanbun.feature.tanbun.repo import search_tanbun
from tanbun.feature.tanbun.repo.clause import OrderBy
from tanbun.feature.user.label import LUser


@async_fixture()
async def u() -> LUser:  # noqa: D103
    return await LUser(email="onex@gmail.com", hashed_password="xxx").save()  # noqa: S106


@mark_async_test()
async def test_pagerank_search(u: LUser):
    """リソース内の有効な順位のみ使い、ページング前にソートする."""
    _, resource = await save_text(u.uid, "# rank search\n  low\n  high\n  missing\n")
    await save_text(u.uid, "# other resource\n  foreign\n")
    await adb.cypher_query(
        """
        MATCH (r:Resource {uid:$uid})
        SET r.pagerank_version=1, r.pagerank_source_hash=r.txt_hash,
            r.pagerank_source_updated=r.updated
        WITH r MATCH (s:Sentence {resource_uid:r.uid})
        SET s.pagerank_score=CASE s.val WHEN 'low' THEN 0.5
            WHEN 'high' THEN 2.0 ELSE null END
        """,
        params={"uid": resource.uid.hex},
    )
    result = await search_tanbun(
        "",
        filter_resource_uids=[resource.uid],
        sort="pagerank",
    )
    assert [item.sentence for item in result.data] == ["high", "low", "missing"]
    assert list(result.pagerank_scores.values()) == [2.0, 0.5, None]
    page = await search_tanbun(
        "",
        paging=Paging(page=2, size=1),
        filter_resource_uids=[resource.uid],
        sort="pagerank",
    )
    assert page.data[0].sentence == "low"
    ascending = await search_tanbun(
        "",
        order_by=OrderBy(desc=False),
        filter_resource_uids=[resource.uid],
        sort="pagerank",
    )
    assert [item.sentence for item in ascending.data] == ["low", "high", "missing"]
    normal = await search_tanbun("", filter_resource_uids=[resource.uid])
    assert normal.pagerank_scores == {}
    client = TestClient(root_router())
    assert (
        client.get("/tanbun/?sort=pagerank").status_code
        == status.HTTP_422_UNPROCESSABLE_ENTITY
    )
    response = client.get(f"/tanbun/?sort=pagerank&resource_id={resource.uid}&q=high")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["data"][0]["sentence"] == "high"
    assert list(response.json()["pagerank_scores"].values()) == [2.0]
    await adb.cypher_query(
        "MATCH (r:Resource {uid:$uid}) SET r.pagerank_source_hash=-1",
        params={"uid": resource.uid.hex},
    )
    stale = await search_tanbun(
        "",
        filter_resource_uids=[resource.uid],
        sort="pagerank",
    )
    assert all(value is None for value in stale.pagerank_scores.values())


@mark_async_test()
async def test_detail_router(u: LUser, caplog):
    """Router testは1つはしておくけど細かいケースはrepositoryなどでやっておく."""
    client = TestClient(root_router())
    res = client.get("/tanbun/sentence/064ef00c-5e33-4505-acf5-45ba26cc54dc")
    assert res.status_code == status.HTTP_404_NOT_FOUND

    s = """
    # titleX
    ## head1
    ### head2
        parent
            when. 19C
            p1
            p2
                p21
            p3
    """
    t = datetime.now(tz=pytz.timezone("Asia/Tokyo"))
    _sn, _r = await save_text(
        u.uid,
        s,
        # DB readしたときにneo4j.DateTimeが返ってきて
        # pydanticとvalidate error にならないかチェック
        updated=t,
        path=("A", "B", "C.txt"),
    )

    s = await LSentence.nodes.get(val="p21")
    url = f"/tanbun/sentence/{UUID(s.uid)}"
    res = client.get(url)

    assert res.status_code == status.HTTP_200_OK
    chains = TanbunChains.model_validate(res.json())
    c = chains.root[0]
    assert c.location.resource.updated == t
    assert c.location.user.id.hex == u.uid
    assert [f.val for f in c.location.folders] == ["A", "B"]


@mark_async_test()
async def test_search(u: LUser, caplog):
    """本番でのapi callを模擬(重いからskip."""
    s = """
    # 神は数学者か?
      @author マオリ・リヴィオ
      @published 2017

    ## 1. 謎

      数学の偏在性と全能性:

      「宇宙はまるで純粋数学者が設計したかのようだ」
        by. ジェームズ・ジーンズ
          イギリスの宇宙物理学者 1877-1946

      P1 |「数学は、経験とは無関係な思考の産物なのに、\
        なぜ物理的実在の対称物にこれほどうまく適合するのか」

      リヴァイアサンの記述
        真理とは、私達が断定を行う際に名称を正しく並べること
        幾何学は神が人類に与え給うた唯一の学問
          定義: 言葉の意味を定める
            計算の初めに置く

      ロジャー・ペンローズ=ペンローズ: オックスフォード大学の著名な数理物理学者

      ペンローズの３世界:
        1. 精神世界: 人間の表象の世界
        2. 物質世界: 物理的実在の世界
        3. 数学世界: プラトン主義の数学的形式の世界、イデア界
          ex. ユークリッド幾何学、ニュートンの運動法則、ひも理論などの数学的モデル

      アルキメデス:
        when. -287 ~ -212
        近代のニュートンやガウスレベルの人物
        父は天文学者ペイディアス、太陽と月の直径比を推定した
        若い頃にアレクサンドリアで数学を学んだ後に故郷シュラクサイに戻って研究に没頭
          食事や体の世話も疎かに研究
        抽象数学の美を名誉とし、それ以外の機械など技術一般は実用目的の卑賤なもの
    """
    client = TestClient(root_router())
    _sn, _r = await save_text(u.uid, s)
    term = await LTerm.nodes.get(val="アルキメデス")
    s = await term.sentence.single()
    url = "/tanbun/?q=%E6%95%B0%E5%AD%A6&type=CONTAINS&page=1&size=100&n_detail=1&n_premise=3&n_conclusion=3&n_refer=3&n_referred=3&dist_axiom=1&dist_leaf=1&desc=true"  # noqa: E501
    res = client.get(url)
    assert res.status_code == status.HTTP_200_OK


@mark_async_test()
async def test_not_found_should_not_raise_error(u: LUser):
    """Regression test."""
    s = """
    # 神は数学者か?
      @author マオリ・リヴィオ
      @published 2017

    ## 1. 謎

      数学の偏在性と全能性:

      「宇宙はまるで純粋数学者が設計したかのようだ」
        by. ジェームズ・ジーンズ
          イギリスの宇宙物理学者 1877-1946

      P1 |「数学は、経験とは無関係な思考の産物なのに、\
        なぜ物理的実在の対称物にこれほどうまく適合するのか」

      ロジャー・ペンローズ=ペンローズ: オックスフォード大学の著名な数理物理学者

      ペンローズの３世界:
        精神世界: 人間の表象の世界
        物質世界: 物理的実在の世界
        数学世界: プラトン主義の数学的形式の世界、イデア界
          ex. ユークリッド幾何学、ニュートンの運動法則、ひも理論などの数学的モデル

      ペンローズの３つの謎:
        {物質世界}が{数学世界}の法則に従う
          ex. `P1`
          ! EF = EFfective
          ex. EF |数学の不条理な有効性:
            物理学の法則を定式化するのに数学という言語が似つかわしいというこの奇跡
            {物質世界}が{数学世界}に従うという謎
            by. ユージン・ウィグナー, ウィグナー: ノーベル賞を受賞した物理学者
              when. 1902 ~ 1995
        心が{物質世界}から生まれる
        {ペンローズの３世界}が不思議なほど結びついている

      積極的な側面: 数学を応用して自然法則を構築するケース
        {EF}を信じて{物質世界}から数学的法則を導き出すこと
        ex. マクスウェル方程式、一般相対性理論
        <-> 受動的な側面: 数学が意図せずに{物質世界}に応用されること
          ハーディ: 純粋数学が{物質世界}に応用されるとは信じていなかった
            イギリスの変わり者の数学者
            when. 1877 ~ 1947
            「数論を戦争に役立たせる道は誰も見出していない」
              <-> 暗号に応用され
          ! nameの区切り文字を","に変えるべきか
    """
    _sn, _r = await save_text(u.uid, s)
    tgt = await LSentence.nodes.first(val="物理的実在の世界")
    client = TestClient(root_router())
    res = client.get(f"/tanbun/sentence/{tgt.uid}")
    assert res.is_success
    uid = UUID(tgt.uid)
    res = client.get(f"/tanbun/sentence/{uid}")
    assert res.is_success


@mark_async_test()
async def test_location_by_regression(u: LUser):
    """<- by. の連鎖ではlocationが not foundになっていたので修正."""
    s = """
    # title
      ZF公理系, ツェルメロ=フレンケルの公理系: 集合論を矛盾なく公理化・再現した
        by. エルンスト・ツェルメロ: ドイツの数学者
          when. 1871 ~ 1953
      選択公理: 空でない集合各々から要素を選び出して新しい集合を作れる
      連続体仮説: 自然数の濃度と実数の濃度の中間は存在しない
      複数の妥当な集合論が成立
        <- {選択公理}と{連続体仮説}は{ZF公理系}と矛盾しない
          by. クルト・ゲーテル, ゲーテル: 最も偉大な論理学者の一人
            when. 1906 ~ 1978
    """

    _sn, _r = await save_text(u.uid, s)
    client = TestClient(root_router())

    tgt = await LSentence.nodes.first(val="ドイツの数学者")
    res = client.get(f"/tanbun/sentence/{tgt.uid}")
    assert res.is_success

    tgt = await LSentence.nodes.first(val="最も偉大な論理学者の一人")
    res = client.get(f"/tanbun/sentence/{tgt.uid}")
    assert res.is_success
