"""戦闘リクエストと計算用コンテキスト。取得・保存・通知を含めない."""

from pydantic import BaseModel, Field

from .balance_models import GameBalance
from .catalog import ElementDefinition
from .enemy_gambits import EnemyBattleRule
from .rewards import GoldSettings
from .skill_definitions import StoredSkill


class EncounterRequest(BaseModel):
    """移動の保存後に遭遇を確定する。checkpointは実際のマップ上に限る."""

    revision: int = Field(ge=0)
    region: int = Field(ge=0, le=10000)
    checkpoint: str = Field(default="@entrance", max_length=64)
    discovered_place: str | None = Field(default=None, max_length=64)
    scheduled: bool = False


class TurnRequest(BaseModel):
    """敵HPは送信も保存もしない。撃破判定はブラウザのゲーム状態."""

    battle_id: str = Field(max_length=64)
    turn: int = Field(ge=0)
    answers: dict[str, list[str]] = Field(default_factory=dict, max_length=20)
    defeated: list[str] = Field(default_factory=list, max_length=20)
    retreat: bool = False


class EnemyStats(BaseModel):
    """固定IDと固定クイズから、その時点の能力を導出する."""

    id: str
    name: str
    base_name: str | None = None
    quizIndex: int  # noqa: N815
    quizIndexes: list[int] = Field(default_factory=list)  # noqa: N815
    hp: int
    attack: int
    mp: int = Field(default=0, ge=0, le=100000)
    defense: int = Field(default=0, ge=0, le=100000)
    special_attack: int | None = Field(default=None, ge=0, le=100000)
    special_defense: int = Field(default=0, ge=0, le=100000)
    unmodified_attack: int | None = None
    speed: int = Field(default=10, ge=1, le=100000)
    relations: int
    region: int
    icon: str | None = None
    animation: str = "none"
    sound: str = "auto"
    on_hit_skill: StoredSkill | None = None
    attack_skill: StoredSkill | None = None
    gambits: list[EnemyBattleRule] = Field(default_factory=list, max_length=20)
    weaknesses: list[str] = Field(default_factory=list)
    resistances: list[str] = Field(default_factory=list)


class CombatContext(BaseModel):
    """敵の能力は保存した値ではなく最新のPowerと関係数を使う."""

    balance: GameBalance
    enemies: list[EnemyStats]
    default_attack: StoredSkill | None = None
    enabled_elements: list[str] | None = None
    elements: list[ElementDefinition] = Field(default_factory=list)
    gold: GoldSettings = Field(default_factory=GoldSettings)
    resource_power: int = 0
    achievement: int = 0
