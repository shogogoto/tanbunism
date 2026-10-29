"""使用方法とは独立した、所持品の表示カテゴリ."""

from enum import StrEnum


class ItemCategory(StrEnum):
    """装備・消耗の動作を変えずに一覧を整理する."""

    WEAPON = "weapon"
    ARMOR = "armor"
    RECOVERY = "recovery"
    UNLOCK_KEY = "unlock_key"
    OTHER = "other"
