"""キューの一意な調停ノード。制約はschema-installで登録."""

from neomodel import AsyncStructuredNode, StringProperty, UniqueIdProperty


class LPageRankQueue(AsyncStructuredNode):
    """複数Webプロセスでも同じロックを使う."""

    __label__ = "PageRankQueue"
    key = StringProperty(unique_index=True, required=True)


class LPageRankJob(AsyncStructuredNode):
    """待機・実行中のジョブ検索を小さく保つ."""

    __label__ = "PageRankJob"
    uid = UniqueIdProperty()
    status = StringProperty(index=True)
