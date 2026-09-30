"""txtからtitleだけを取得する.

いちいちテキスト全てをparseしてtree, graphに変換するのが重い
それを回避したい
"""

from tanbun.feature.domain.errors import DomainError


class NonTitleError(DomainError):
    """タイトルが見つからない."""


def title_parse(text: str) -> str:
    """txtからtitleだけを取得する.

    Args:
        text: markdown text.

    Returns:
        The first line of title.

    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped

    raise NonTitleError


def meta_parse(text: str, mark: str) -> list[str]:
    """txtから指定したmeta情報を取得する.

    Args:
        text: markdown text.
        mark: meta情報のマーク

    Returns:
        The first line of meta.

    """
    metas = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(mark):
            value = stripped[len(mark) :].strip()
            metas.append(value.removeprefix(":").strip())
    return metas
