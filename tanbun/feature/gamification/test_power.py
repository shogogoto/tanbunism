"""Powerの件数・重み付けの単体テスト."""

import pytest
from pydantic import ValidationError

from .power import PowerWeights, calculate_power


def test_power_default_weights() -> None:
    """規模と関係数の単純な重み付き合計."""
    assert calculate_power(10, 4, 2, 3, PowerWeights()) == 26  # noqa: PLR2004


def test_zero_weights_disable_power_without_changing_facts() -> None:
    """全項目をゼロ重みにしても計数値は変更しない."""
    assert (
        calculate_power(
            10,
            4,
            2,
            3,
            PowerWeights(sentence=0, term=0, logic=0, reference=0, abstraction=0),
            abstraction_count=5,
        )
        == 0
    )


def test_abstraction_weight_is_independent() -> None:
    """具体・抽象の件数を既定2点で加算し、重み0で無効化する."""
    assert calculate_power(10, 4, 2, 3, PowerWeights(), abstraction_count=5) == 36  # noqa: PLR2004
    assert (
        calculate_power(10, 4, 2, 3, PowerWeights(abstraction=0), abstraction_count=5)
        == 26  # noqa: PLR2004
    )


@pytest.mark.parametrize("value", [-1, 1.5, True, "2", 10001])
@pytest.mark.parametrize("key", list(PowerWeights.model_fields))
def test_power_weights_reject_invalid_values(key: str, value: object) -> None:
    """重みは範囲内の非負整数だけを受け付ける."""
    with pytest.raises(ValidationError):
        PowerWeights(**{key: value})
