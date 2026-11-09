"""時系列."""

from functools import lru_cache

from edtf import EDTFObject, parse_edtf
from japanera import EraDate

from tanbun.feature.parsing.primitive.time.errors import ParseWhenError
from tanbun.feature.parsing.primitive.time.parse.const import (
    P_CENTURY,
    P_NUMBER,
    MagicTime,
    Season,
    century2span,
)
from tanbun.feature.parsing.primitive.time.parse.parsing import p_interval, p_jp


def parse_extime(s: str) -> EDTFObject:
    """EDTF独自拡張."""
    formatted = str2edtf(s)
    ret = parse_edtf(formatted)
    if isinstance(ret, EDTFObject):
        return ret
    msg = "EDTF表現に変換できません"
    raise ParseWhenError(msg, s)


def jp2edtf(string: str) -> str:
    """和暦をEDTFへ変換.

    令和から明治までの頭文字 ex. R, H, S, T, M で指定できる
    string: H
    """
    res = p_jp().searchString(string)
    if len(res) == 0:
        return string
    s = string.strip().replace("/", "-").split("-")
    s = "/".join([e.zfill(2) for e in s])
    ls = res[0]
    match len(ls):
        case 2:
            era = EraDate.strptime(s, "%-h%-y")[-1]
            return era.strftime("%Y")
        case 4:
            era = EraDate.strptime(s, "%-h%-y/%m")[-1]
            return era.strftime("%Y-%m")
        case 6:
            era = EraDate.strptime(s, "%-h%-y/%m/%d")[-1]
            return era.strftime("%Y-%m-%d")
        case _:  # マッチしない
            raise ValueError(string)


@lru_cache(maxsize=256)
def str2edtf(string: str) -> str:
    """文字列をEDTF(Extended DateTime Format)に変換.

    区切り文字サポート [/-] e.g. yyyy[/MM[/dd]]
    """
    s = string.strip()
    if MagicTime.BC in s:
        s = s.replace(MagicTime.BC, "")
        s = f"-{s}"

    # 20C -> 1901/2000 が厳密だが、19XXでよくね? 世紀
    s, season = Season.replace(s)
    if P_CENTURY.matches(s):
        c0, c1 = century2span(s)
        if season is not None:
            c0 += season.offset
            c1 = min(c0 + season.width, c1)

        return "/".join([f"{c:04}" if c >= 0 else f"{c:05}" for c in [c0, c1]])

    if p_interval().matches(s):
        return s

    if p_jp().matches(s):
        return jp2edtf(s)

    if s[0] == "-":
        ymd = s[1:].replace("/", "-").split("-", maxsplit=1)
        ymd[0] = f"-{ymd[0]}"
    else:
        ymd = s.replace("/", "-").split("-", maxsplit=1)
    match len(ymd):
        case 1:
            return _to_year_edtf(ymd[0])
        case 2:
            y, md = ymd
            y = _to_year_edtf(y)
            md = [e.zfill(2) for e in md.split("-")]
            return "-".join([y, *md])
        case _:
            raise ValueError(string)


def _to_year_edtf(s: str) -> str:
    """年のEDTF形式変換."""
    if P_NUMBER.matches(s):
        y = int(s)
        if abs(y) >= 10000:  # noqa: PLR2004
            return f"Y{y}"
        return f"{y:05}" if y < 0 else f"{y:04}"
    # s2 = text_to_edtf(s)
    # if s2 is None:
    #     msg = f"'{s}'は年のフォーマットと合わない"
    #     raise ValueError(msg)
    return s
