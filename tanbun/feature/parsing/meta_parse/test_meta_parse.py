"""test title parse."""

from pytest_unordered import unordered

from tanbun.feature.parsing.meta_parse import meta_parse, title_parse


def test_title_parse():
    """Title parse."""
    assert title_parse("# title") == "# title"

    assert (
        title_parse(
            """


        # title1
    """,
        )
        == "# title1"
    )

    assert (
        title_parse(
            """
        # title2
            aaaa
            xxx
            yyy
    """,
        )
        == "# title2"
    )


def test_author_parse():
    """authorを取得."""
    assert meta_parse(
        """
        # title
            @author John Due
            """,
        "@author",
    ) == ["John Due"]

    assert meta_parse(
        """
        # title
            @author John Due
            @author Jane Due
            """,
        "@author",
    ) == unordered(["John Due", "Jane Due"])


def test_meta_parse_accepts_legacy_colon_separator():
    """Legacy metadata with a colon is parsed consistently with tree parsing."""
    assert meta_parse("@published: 2016-7-10", "@published") == ["2016-7-10"]
    assert meta_parse("@author: John Due", "@author") == ["John Due"]
