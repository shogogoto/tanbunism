"""スキル・効果の型と、保存済みスキルの復元。カタログ全体には依存しない."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    TypeAdapter,
    model_serializer,
    model_validator,
)

from .catalog_identifiers import DefinitionId, IconId
from .effect_chance import FULL_CHANCE


class SkillEffect(StrEnum):
    """スキルの共通効果。JSONでは読みやすい文字列を維持する."""

    ATTACK = "attack"
    HEAL_HP = "heal_hp"
    HEAL_MP = "heal_mp"
    DAMAGE_MP = "damage_mp"
    RECOVER_MOVES = "recover_moves"
    EQUIP_ATTACK = "equip_attack"
    BUFF = "buff"
    DEBUFF = "debuff"
    AILMENT = "ailment"


class SkillTarget(StrEnum):
    """効果の対象。利用者や入手経路とは独立する."""

    ENEMY = "enemy"
    ALL_ENEMIES = "all_enemies"
    SELF = "self"


class AmountMode(StrEnum):
    """効果量の指定方式."""

    FIXED = "fixed"
    PERCENT = "percent"


class FizzleResult(BaseModel):
    """誤答では効果なしと共通反撃。MPやアイテムの消費は取り消さない。."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["fizzle"] = "fizzle"


class CounterResult(BaseModel):
    """誤答時に対象一体から通常攻撃で反撃される。."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["counter"] = "counter"


class ReducedResult(BaseModel):
    """誤答時の効果量を直接指定する。共通反撃は別途一度だけ適用する。."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["reduced"] = "reduced"
    amount: int = Field(default=0, ge=0, le=10000)
    amount_mode: AmountMode | None = None

    @model_serializer(mode="wrap")
    def configured_fields(self, handler):
        """拡張指定がない旧形式には未設定キーを増やさない."""
        return {key: value for key, value in handler(self).items() if value is not None}


class ReducedAttackResult(ReducedResult):
    """攻撃だけは誤答時の倍率も指定できる。."""

    multiplier: float = Field(default=0.5, ge=0, le=10, allow_inf_nan=False)


class ReducedTimedResult(ReducedResult):
    """持続効果では誤答時の有効行動数も独立して指定する."""

    duration_turns: Annotated[int, Field(ge=0, le=20)] | Literal["infinite"] | None = (
        None
    )


class ReducedAilmentResult(BaseModel):
    """状態異常は確率ではなく、付与するかどうかだけを指定する。."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["reduced"] = "reduced"
    apply: bool = False
    duration_turns: Annotated[int, Field(ge=0, le=20)] | Literal["infinite"] | None = (
        None
    )

    @model_serializer(mode="wrap")
    def configured_fields(self, handler):
        """旧付与設定へ未指定の持続項目を追加しない."""
        return {key: value for key, value in handler(self).items() if value is not None}


NumericWrongResult = Annotated[
    CounterResult | FizzleResult | ReducedResult,
    Field(discriminator="type"),
]
TimedWrongResult = Annotated[
    CounterResult | FizzleResult | ReducedTimedResult,
    Field(discriminator="type"),
]


class SkillStat(StrEnum):
    """持続効果が変更する能力."""

    ATTACK = "attack"
    DEFENSE = "defense"
    MAX_HP = "maxHp"
    MAX_MP = "maxMp"
    SPEED = "speed"
    SPECIAL_ATTACK = "specialAttack"
    SPECIAL_DEFENSE = "specialDefense"
    DAMAGE_DEALT = "damageDealt"
    DAMAGE_TAKEN = "damageTaken"


class SkillSound(StrEnum):
    """スキルの効果音。自動では効果種別に合う音を使う."""

    AUTO = "auto"
    NONE = "none"
    WILLO_CAST = "willo_cast"
    WILLO_IMPACT = "willo_impact"
    WILLO_FIRE_CAST = "willo_fire_cast"
    WILLO_FIRE_IMPACT = "willo_fire_impact"
    WILLO_ICE_CAST = "willo_ice_cast"
    WILLO_ICE_SHATTER = "willo_ice_shatter"
    WILLO_ZAP = "willo_zap"
    WILLO_THUNDER = "willo_thunder"
    WILLO_WATER_CAST = "willo_water_cast"
    WILLO_WATER_IMPACT = "willo_water_impact"
    WILLO_WIND = "willo_wind"
    WILLO_POISON = "willo_poison"
    WILLO_SHADOW = "willo_shadow"
    WILLO_HEAL = "willo_heal"
    WILLO_BUFF = "willo_buff"
    WILLO_DEBUFF = "willo_debuff"
    WILLO_MANA = "willo_mana"
    WILLO_DISPEL = "willo_dispel"
    WILLO_ARCANE_BLAST = "willo_arcane_blast"
    WILLO_RUNE = "willo_rune"
    IMPACT = "impact"
    SLASH = "slash"
    FIRE = "fire"
    LIGHTNING = "lightning"
    HEAL = "heal"
    BUFF = "buff"
    DEBUFF = "debuff"
    POISON = "poison"
    CURSE = "curse"
    SLASH_2 = "slash_2"
    SLASH_3 = "slash_3"
    FIRE_2 = "fire_2"
    FIRE_3 = "fire_3"
    FIRE_4 = "fire_4"
    FIRE_5 = "fire_5"
    FIRE_6 = "fire_6"
    FIRE_7 = "fire_7"
    LIGHTNING_2 = "lightning_2"
    LIGHTNING_STORM = "lightning_storm"
    HEAL_2 = "heal_2"
    HEAL_3 = "heal_3"
    HEAL_4 = "heal_4"
    HEAL_5 = "heal_5"
    HEAL_6 = "heal_6"
    HEAL_7 = "heal_7"
    POISON_2 = "poison_2"
    POISON_3 = "poison_3"
    POISON_4 = "poison_4"
    MAGIC = "magic"
    METAL = "metal"
    METAL_2 = "metal_2"
    COINS = "coins"
    STONES = "stones"
    FIRE_BURST = "fire_burst"
    FIRE_BURST_2 = "fire_burst_2"
    FIRE_BURST_3 = "fire_burst_3"
    ICE = "ice"
    ICE_2 = "ice_2"
    ICE_3 = "ice_3"
    ICE_4 = "ice_4"
    ICE_5 = "ice_5"
    WATER = "water"
    WATER_2 = "water_2"
    WATER_3 = "water_3"
    WATER_4 = "water_4"
    WATER_5 = "water_5"
    EARTH = "earth"
    DARK_2 = "dark_2"
    DARK_3 = "dark_3"
    DRAIN = "drain"
    TELEPORT = "teleport"
    CHIME = "chime"
    CHIME_2 = "chime_2"
    CHIME_3 = "chime_3"
    CHIME_4 = "chime_4"
    WIND = "wind"
    EXPLOSION = "explosion"
    EXPLOSION_2 = "explosion_2"
    ZAP = "zap"
    ZAP_2 = "zap_2"
    MAGIC_RISE = "magic_rise"


class SkillBase(BaseModel):
    """種類に依存しない識別・表示・消費MP・演出だけを共有する."""

    model_config = ConfigDict(extra="forbid")

    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    icon: IconId = "sparkles"
    enabled: bool = True
    mp_cost: int = Field(default=0, ge=0, le=10000)
    template_id: DefinitionId | None = None
    template_ids: list[DefinitionId] = Field(default_factory=list, max_length=20)
    effect_chances: dict[
        DefinitionId,
        Annotated[int, Field(strict=True, ge=0, le=100)],
    ] = Field(default_factory=dict)
    _activation_chance: int = PrivateAttr(default=100)
    _chance_key: str = PrivateAttr(default="")
    effects: list["EffectDefinition"] = Field(default_factory=list, max_length=20)
    element: DefinitionId = "none"

    @model_validator(mode="after")
    def valid_effect_chances(self):
        """確率はスキルが参照する効果だけに指定できる。."""
        references = set(
            self.template_ids or ([self.template_id] if self.template_id else []),
        )
        if self.effect_chances.keys() - references:
            msg = "発動率には参照中の効果テンプレートを指定してください。"
            raise ValueError(msg)
        if self.id == "normal_attack" and any(
            value != FULL_CHANCE for value in self.effect_chances.values()
        ):
            msg = "戦うの発動率は100%にしてください。"
            raise ValueError(msg)
        return self

    @property
    def activation_chance(self):
        """解決済みの一効果だけが持つ発動率。."""
        return self._activation_chance

    @property
    def chance_key(self):
        """異なるスキルからの同一効果も別の抽選として区別する。."""
        return self._chance_key or self.id

    def with_activation(self, chance: int, key: str):
        """保存定義を変えず、解決済み効果へ発動率を渡す。."""
        selected = self.model_copy(deep=True)
        selected._activation_chance, selected._chance_key = chance, key  # noqa: SLF001 - same class snapshot
        return selected

    sound: SkillSound = SkillSound.AUTO
    animation: Literal[
        "willo_ice_shatter",
        "willo_zap",
        "willo_fire_impact",
        "willo_cast",
        "willo_heal",
        "willo_impact",
        "oga_star_ice",
        "oga_star_thunder",
        "oga_star_fire",
        "none",
        "slash",
        "impact",
        "fire",
        "lightning",
        "heal",
        "ripple",
        "buff",
        "debuff",
        "poison",
        "oga_slash_1",
        "oga_slash_2",
        "oga_slash_3",
        "oga_slash_4",
        "oga_slash_5",
        "oga_spell_1",
        "oga_spell_2",
        "oga_spell_3",
        "oga_spell_4",
        "oga_spell_5",
        "oga_spell_6",
        "oga_spell_7",
        "oga_spell_8",
        "oga_spell_9",
        "oga_spell_10",
        "oga_effect_1",
        "oga_effect_2",
        "oga_effect_3",
        "oga_effect_4",
        "oga_effect_5",
        "oga_nature_1",
        "oga_nature_2",
        "oga_nature_3",
        "oga_nature_4",
        "oga_arcane_1",
        "oga_arcane_2",
        "oga_arcane_3",
        "oga_arcane_4",
        "oga_arcane_5",
        "oga_arcane_6",
        "oga_earth_1",
        "oga_earth_2",
        "oga_earth_3",
        "oga_earth_4",
        "oga_earth_5",
        "oga_cosmic_1",
        "oga_cosmic_2",
        "oga_cosmic_3",
        "oga_cosmic_4",
        "oga_cosmic_5",
        "mochi_status_curse",
        "mochi_status_buff",
        "mochi_status_shield",
        "mochi_status_heal",
        "mochi_status_speed",
    ] = "none"


class EffectBase(BaseModel):
    """表示情報とは独立した効果の設定."""

    model_config = ConfigDict(extra="forbid")
    target: SkillTarget = SkillTarget.SELF
    target_count: int | Literal["all"] = Field(default=1)
    damage_type: Literal["physical", "special"] = "physical"

    @model_validator(mode="after")
    def valid_target_count(self):
        """旧2・4体の定義も保持し、新しい全体指定を受け入れる."""
        maximum_targets = 5
        if self.target_count != "all" and not 1 <= self.target_count <= maximum_targets:
            msg = "対象数は1から5またはallで指定してください。"
            raise ValueError(msg)
        return self


class VariableAmountEffect(EffectBase):
    """固定値または割合で効果量を指定する種類の共通項目."""

    amount: int = Field(default=0, ge=0, le=10000)
    amount_mode: AmountMode = AmountMode.FIXED


class AttackEffect(VariableAmountEffect):
    """攻撃。加算ダメージと攻撃力に掛ける倍率を持つ."""

    effect: Literal[SkillEffect.ATTACK] = SkillEffect.ATTACK
    target: SkillTarget = SkillTarget.ENEMY
    multiplier: float = Field(default=1, ge=0, le=10, allow_inf_nan=False)
    damage_type: Literal["physical", "special"] = "physical"
    on_wrong: Annotated[
        CounterResult | FizzleResult | ReducedAttackResult,
        Field(discriminator="type"),
    ] = Field(default_factory=FizzleResult)


class RecoveryEffect(VariableAmountEffect):
    """HP・MP回復とMP減少。攻撃倍率や持続効果は持たない."""

    effect: Literal[SkillEffect.HEAL_HP, SkillEffect.HEAL_MP, SkillEffect.DAMAGE_MP]
    target: SkillTarget = SkillTarget.SELF
    on_wrong: NumericWrongResult = Field(default_factory=FizzleResult)


class TimedEffect(VariableAmountEffect):
    """戦闘中に持続する効果の期間と重複上限."""

    duration_turns: Annotated[int, Field(ge=1, le=20)] | Literal["infinite"] = 3
    stack_limit: int = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def valid_element_modifier(self):
        """属性補正は属性と比率を必須にし、能力バフとは混ぜない."""
        if getattr(self, "stat", None) in {"damageDealt", "damageTaken"}:
            if (
                not self.affected_element
                or self.affected_element == "none"
                or self.amount_mode != "percent"
            ):
                msg = "属性補正には対象属性と比率を指定してください。"
                raise ValueError(msg)
        elif getattr(self, "affected_element", None):
            msg = "対象属性は属性ダメージ補正だけに指定できます。"
            raise ValueError(msg)
        return self

    @model_serializer(mode="wrap")
    def relevant_element_fields(self, handler):
        """属性補正以外には不要な対象属性を出力しない."""
        values = handler(self)
        if values.get("stat") not in {"damageDealt", "damageTaken"}:
            values.pop("affected_element", None)
        return values


class StatEffect(TimedEffect):
    """能力の上昇・低下に共通する設定."""

    stat: SkillStat = SkillStat.ATTACK
    affected_element: DefinitionId | None = None
    on_wrong: TimedWrongResult = Field(default_factory=FizzleResult)

    @model_validator(mode="after")
    def valid_resource_scale(self):
        """HP/MPは最大値と残量を同じ比率で変更する."""
        if self.stat in {"maxHp", "maxMp"} and self.amount_mode != "percent":
            msg = "HP/MPの能力変更は割合で指定してください。"
            raise ValueError(msg)
        return self


class BuffEffect(StatEffect):
    """能力の向上."""

    effect: Literal[SkillEffect.BUFF] = SkillEffect.BUFF
    target: SkillTarget = SkillTarget.SELF


class DebuffEffect(StatEffect):
    """能力の低下。対象ステータスはbuffと共通."""

    effect: Literal[SkillEffect.DEBUFF] = SkillEffect.DEBUFF
    target: SkillTarget = SkillTarget.ENEMY


class AilmentEffect(TimedEffect):
    """状態異常。能力を指定するstatとは独立する."""

    effect: Literal[SkillEffect.AILMENT] = SkillEffect.AILMENT
    target: SkillTarget = SkillTarget.ENEMY
    amount: int = Field(default=5, ge=0, le=10000)
    ailment: Literal["poison", "curse", "regen", "refresh"] = "poison"
    on_wrong: Annotated[
        CounterResult | FizzleResult | ReducedAilmentResult,
        Field(discriminator="type"),
    ] = Field(default_factory=FizzleResult)


class UtilityEffect(EffectBase):
    """固定値の歩数回復・装備効果。割合や持続ターンは持たない."""

    effect: Literal[SkillEffect.RECOVER_MOVES, SkillEffect.EQUIP_ATTACK]
    target: Literal[SkillTarget.SELF] = SkillTarget.SELF
    amount: int = Field(default=0, ge=0, le=10000)


EffectDefinition = Annotated[
    AttackEffect
    | RecoveryEffect
    | BuffEffect
    | DebuffEffect
    | AilmentEffect
    | UtilityEffect,
    Field(discriminator="effect"),
]


class AttackSkill(SkillBase, AttackEffect):
    """名前・演出を持つ攻撃スキル."""


class RecoverySkill(SkillBase, RecoveryEffect):
    """名前・演出を持つ回復スキル."""


class BuffSkill(SkillBase, BuffEffect):
    """名前・演出を持つバフスキル."""


class DebuffSkill(SkillBase, DebuffEffect):
    """名前・演出を持つデバフスキル."""


class AilmentSkill(SkillBase, AilmentEffect):
    """名前・演出を持つ状態異常スキル."""


class UtilitySkill(SkillBase, UtilityEffect):
    """名前・演出を持つ補助スキル."""


SKILL_IDENTITY_FIELDS = (set(SkillBase.model_fields) - {"effects"}) | {"mp_cost"}


def default_normal_attack() -> AttackSkill:
    """装備枠・MPを消費しない標準行動の初期値."""
    return AttackSkill(
        id="normal_attack",
        name="戦う",
        animation="impact",
        on_wrong=CounterResult(),
    )


SkillDefinition = Annotated[
    AttackSkill | RecoverySkill | BuffSkill | DebuffSkill | AilmentSkill | UtilitySkill,
    Field(discriminator="effect"),
]
StoredSkill = SkillDefinition
skill_adapter = TypeAdapter(StoredSkill)


def parse_skill(value) -> SkillDefinition:
    """現行の種類別スキル定義として検証する."""
    return skill_adapter.validate_python(value)


def validate_effect_combinations(skills) -> None:
    """戦闘の複合効果に、装備や歩数という戦闘外の操作を混ぜない."""
    for skill in skills:
        if len(skill.effects) > 1 and any(
            effect.effect in {"equip_attack", "recover_moves"}
            for effect in skill.effects
        ):
            msg = "装備・歩数回復は他の効果と組み合わせられません。"
            raise ValueError(msg)
