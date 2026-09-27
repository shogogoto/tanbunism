"""行変換."""

from __future__ import annotations

import re
from textwrap import dedent
from typing import Final

ALIAS_SEP: Final = "|"
ALIAS_PATTERN: Final = re.compile(
    rf"^(?P<alias>\S+)(?:\s+{re.escape(ALIAS_SEP)}\s*|"
    rf"\s*{re.escape(ALIAS_SEP)}\s+)(?P<define>.*)$",
)
DEF_SEPS: Final = (":", "\uff1a")
NAME_SEP: Final = ","


# larkでのparseが思ったようにいかなかったから諦めてブサイクに実装
#   aliasやname, sentenceで許容する文字列を思ったように設定できなかった
def parse_line(line: str) -> tuple[str | None, list[str], str | None]:
    """行を解析."""
    txt = dedent(line).strip()
    alias = None
    names = []
    define = txt
    sentence = txt
    alias_match = ALIAS_PATTERN.match(txt)
    if alias_match is not None:
        alias = alias_match.group("alias")
        define = alias_match.group("define")
        sentence = define
    separators = [
        (define.index(separator), separator)
        for separator in DEF_SEPS
        if separator in define
    ]
    if separators:
        index, separator = min(separators)
        names, sentence = define[:index], define[index + len(separator) :]
        names = [n.strip() for n in names.split(NAME_SEP)]
    return alias, names, sentence.strip() if sentence else None
