"""装備箇所と、アイテム定義で指定する装備部位."""

from typing import Literal, cast

EquipmentPart = Literal["arm", "body", "feet", "head", "accessory"]
EquipmentSlot = Literal[
    "arm1",
    "arm2",
    "body",
    "feet",
    "head",
    "accessory1",
    "accessory2",
]


def slot_part(slot: EquipmentSlot) -> EquipmentPart:
    """左右・番号は表示上の区別で、装備可能な部位は共通."""
    return cast(EquipmentPart, slot.rstrip("12"))
