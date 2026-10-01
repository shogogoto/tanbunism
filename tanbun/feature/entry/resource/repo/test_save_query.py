"""Resource保存用Cypherの組み立て."""

from tanbun.feature.entry.resource.repo.save import sysnet2cypher
from tanbun.feature.parsing.tree2net import parse2net


def test_sysnet2cypher_escapes_user_text() -> None:
    """引用符やバックスラッシュを含む読書メモも安全に保存できる."""
    network = parse2net(
        "# title\n  BASIC, Beginner's language: stored at C:\\\\notes\n",
    )

    query = sysnet2cypher(network)

    assert "Beginner\\'s language" in query
    assert "C:\\\\\\\\notes" in query
