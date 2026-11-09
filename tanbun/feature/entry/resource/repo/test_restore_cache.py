"""DBから得た値の変換で、過去の値をキャッシュしない."""

import gc
import weakref
from unittest.mock import MagicMock

from .restore import to_tanbun


def test_restore_uses_current_properties_without_retaining_the_db_node() -> None:
    """同一ノードの内容変更を反映し、接続由来のオブジェクトを保持しない."""
    properties = {"val": "before"}
    node = MagicMock()
    node.labels = frozenset({"Sentence"})
    node.get.side_effect = properties.get
    reference = weakref.ref(node)
    assert to_tanbun(node) == "before"
    properties["val"] = "after"
    assert to_tanbun(node) == "after"
    del node
    gc.collect()
    assert reference() is None
