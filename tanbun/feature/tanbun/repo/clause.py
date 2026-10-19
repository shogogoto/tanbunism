"""句."""

from enum import StrEnum

from pydantic import BaseModel


class WherePhrase(StrEnum):
    """WHERE句の条件."""

    CONTAINS = "CONTAINS"
    STARTS_WITH = "STARTS WITH"
    ENDS_WITH = "ENDS WITH"
    REGEX = "=~"
    EQUAL = "="


class OrderBy(BaseModel):
    """ORDER BY句."""

    n_detail: int = 1
    n_premise: int = 1
    n_conclusion: int = 1
    n_refer: int = 1
    n_referred: int = 1
    n_abstract: int = 1
    n_example: int = 1
    n_anti: int = 3
    desc: bool = True  # スコアの高い順がデフォルト

    def score_prop(self) -> str:
        """スコアの計算式."""
        qs = []
        prefix = ""
        for k, v in self:
            if v == 0 or k in {"score", "desc"}:  # スコアは省略
                continue
            if v == 1:  # 重み1のときは省略
                qs.append(f"{prefix}{k}")
            else:
                qs.append(f"({v:+} * {prefix}{k})")
        line = " + ".join(qs)
        return f", score: {line}"

    def phrase(self) -> str:
        """ORDER BY句."""
        return f"""
        ORDER BY stats.score {"DESC" if self.desc else "ASC"}
        """
