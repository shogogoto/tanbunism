"""知識量と関係量だけに基づくPowerの規則・運用設定."""

from neomodel import adb
from pydantic import BaseModel, Field


class PowerWeights(BaseModel, frozen=True):
    """非負整数の重み。Lv・XPとは独立."""

    sentence: int = Field(default=1, ge=0, le=10000, strict=True)
    term: int = Field(default=1, ge=0, le=10000, strict=True)
    logic: int = Field(default=3, ge=0, le=10000, strict=True)
    reference: int = Field(default=2, ge=0, le=10000, strict=True)


def calculate_power(
    sentence_count: int,
    term_count: int,
    logic_count: int,
    reference_count: int,
    weights: PowerWeights,
) -> int:
    """適用中の重みと件数からPowerを計算する."""
    return (
        sentence_count * weights.sentence
        + term_count * weights.term
        + logic_count * weights.logic
        + reference_count * weights.reference
    )


async def get_power_weights() -> PowerWeights:
    """保存した重みを取得する。未設定の項目は既定値を使う."""
    rows, _ = await adb.cypher_query(
        """
        MATCH (settings:AdminSettings {key: 'resource_power'})
        RETURN settings.sentence, settings.term, settings.logic, settings.reference
        LIMIT 1
        """,
    )
    if not rows:
        return PowerWeights()
    return PowerWeights(**{
        key: value
        for key, value in zip(PowerWeights.model_fields, rows[0], strict=True)
        if value is not None
    })


async def update_power_weights(weights: PowerWeights) -> PowerWeights:
    """重みを永続化する。既存リソースにも次回取得から適用される."""
    await adb.cypher_query(
        """
        MERGE (settings:AdminSettings {key: 'resource_power'})
        SET settings.sentence = $sentence, settings.term = $term,
            settings.logic = $logic, settings.reference = $reference
        """,
        params=weights.model_dump(),
    )
    return weights
