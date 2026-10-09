"""キャッシュ鮮度判定を推薦と管理で共有."""

VERSION = 1


def current_cache(resource: str) -> str:
    """内容hash・更新日時・計算規則が揃う場合だけ有効."""
    return (
        f"{resource}.pagerank_version = {VERSION} AND "
        f"{resource}.pagerank_source_hash = {resource}.txt_hash AND "
        f"{resource}.pagerank_source_updated = {resource}.updated"
    )


def cached_rank(sentence: str, resource: str) -> str:
    """未計算・古い結果はnullを返して従来スコアにフォールバック."""
    return (
        f"CASE WHEN {current_cache(resource)} THEN "
        f"{sentence}.pagerank_score ELSE null END"
    )
