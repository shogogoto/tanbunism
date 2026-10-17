"""管理者が定義するスキル・所持品・敵の素材。戦闘開始時に効果を固定する。."""

from enum import StrEnum
from typing import Annotated, Literal

from neomodel import adb
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

DefinitionId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
IconId = Literal[
    "crawl_goblin",
    "crawl_kobold",
    "crawl_orc",
    "crawl_orc_wizard",
    "crawl_ogre",
    "crawl_troll",
    "crawl_jelly",
    "crawl_bat",
    "crawl_wolf",
    "crawl_snake",
    "crawl_scorpion",
    "crawl_mummy",
    "crawl_wraith",
    "crawl_fire_elemental",
    "crawl_dragon",
    "crawl_ice_dragon",
    "crawl_golden_dragon",
    "crawl_hydra",
    "crawl_ancient_lich",
    "crawl_stone_golem",
    "crawl_orc_warlord",
    "crawl_griffon",
    "crawl_sword",
    "crawl_dagger",
    "crawl_axe",
    "crawl_bow",
    "crawl_staff",
    "crawl_shield",
    "crawl_buckler",
    "crawl_spellbook",
    "crawl_red_potion",
    "crawl_blue_potion",
    "crawl_green_potion",
    "crawl_orange_potion",
    "crawl_scroll",
    "crawl_apple",
    "crawl_bread",
    "crawl_honey",
    "crawl_fireball",
    "crawl_freeze",
    "crawl_regeneration",
    "crawl_blink",
    "crawl_chest_closed",
    "crawl_chest_open",
    "crawl_fountain",
    "art_archive_golem",
    "art_crystal_dragon",
    "art_ancient_treant",
    "art_ink_slime",
    "art_scroll_mimic",
    "art_owl_mage",
    "apple",
    "axe",
    "backpack",
    "battery",
    "bone",
    "bomb",
    "castle",
    "mystery",
    "clover",
    "compass",
    "dice",
    "dog",
    "eye",
    "feather",
    "fish",
    "flower",
    "hand",
    "pulse",
    "hourglass",
    "key",
    "leaf",
    "moon",
    "mountain",
    "rabbit",
    "rat",
    "shell",
    "snail",
    "sun",
    "target",
    "tent",
    "trees",
    "turtle",
    "sword",
    "swords",
    "flame",
    "snowflake",
    "zap",
    "sparkles",
    "wand",
    "heart",
    "shield",
    "droplet",
    "flask",
    "footprints",
    "coins",
    "gem",
    "scroll",
    "book",
    "ghost",
    "bug",
    "skull",
    "crown",
    "bird",
    "cat",
]


class SkillEffect(StrEnum):
    """スキルの共通効果。JSONでは読みやすい文字列を維持する."""

    ATTACK = "attack"
    HEAL_HP = "heal_hp"
    HEAL_MP = "heal_mp"
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


class SkillStat(StrEnum):
    """持続効果が変更する能力."""

    ATTACK = "attack"
    DEFENSE = "defense"
    MAX_HP = "maxHp"


class SkillSound(StrEnum):
    """スキルの効果音。自動では効果種別に合う音を使う."""

    AUTO = "auto"
    NONE = "none"
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
    sound: SkillSound = SkillSound.AUTO
    animation: Literal[
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


class VariableAmountSkill(SkillBase):
    """固定値または割合で効果量を指定する種類の共通項目."""

    amount: int = Field(default=0, ge=0, le=10000)
    amount_mode: AmountMode = AmountMode.FIXED


class AttackSkill(VariableAmountSkill):
    """攻撃。加算ダメージと攻撃力に掛ける倍率を持つ."""

    effect: Literal[SkillEffect.ATTACK] = SkillEffect.ATTACK
    target: Literal[SkillTarget.ENEMY, SkillTarget.ALL_ENEMIES] = SkillTarget.ENEMY
    multiplier: float = Field(default=1, ge=0, le=10, allow_inf_nan=False)


class RecoverySkill(VariableAmountSkill):
    """HP・MP回復。攻撃倍率や持続効果は持たない."""

    effect: Literal[SkillEffect.HEAL_HP, SkillEffect.HEAL_MP]
    target: Literal[SkillTarget.SELF] = SkillTarget.SELF


class TimedSkill(VariableAmountSkill):
    """戦闘中に持続する効果の期間と重複上限."""

    duration_turns: int = Field(default=3, ge=1, le=20)
    stack_limit: int = Field(default=1, ge=1, le=10)


class BuffSkill(TimedSkill):
    """自分の能力向上。バブルは最大HPへの割合効果."""

    effect: Literal[SkillEffect.BUFF] = SkillEffect.BUFF
    target: Literal[SkillTarget.SELF] = SkillTarget.SELF
    stat: SkillStat = SkillStat.ATTACK

    @model_validator(mode="after")
    def valid_bubble(self):
        """最大HPを固定値で変更しない."""
        if self.stat == "maxHp" and self.amount_mode != "percent":
            msg = "最大HPの変更は割合buffで指定してください。"
            raise ValueError(msg)
        return self


class DebuffSkill(TimedSkill):
    """敵の能力低下。現在の敵が持つ攻撃力を対象にする."""

    effect: Literal[SkillEffect.DEBUFF] = SkillEffect.DEBUFF
    target: Literal[SkillTarget.ENEMY, SkillTarget.ALL_ENEMIES] = SkillTarget.ENEMY
    stat: Literal[SkillStat.ATTACK] = SkillStat.ATTACK


class AilmentSkill(TimedSkill):
    """状態異常。能力を指定するstatとは独立する."""

    effect: Literal[SkillEffect.AILMENT] = SkillEffect.AILMENT
    target: Literal[SkillTarget.ENEMY, SkillTarget.ALL_ENEMIES] = SkillTarget.ENEMY
    amount: int = Field(default=5, ge=1, le=10000)
    ailment: Literal["poison", "curse"] = "poison"


class UtilitySkill(SkillBase):
    """固定値の歩数回復・装備効果。割合や持続ターンは持たない."""

    effect: Literal[SkillEffect.RECOVER_MOVES, SkillEffect.EQUIP_ATTACK]
    target: Literal[SkillTarget.SELF] = SkillTarget.SELF
    amount: int = Field(default=0, ge=0, le=10000)


SkillDefinition = Annotated[
    AttackSkill | RecoverySkill | BuffSkill | DebuffSkill | AilmentSkill | UtilitySkill,
    Field(discriminator="effect"),
]
SKILL_MODELS = {
    effect: model
    for model, effects in (
        (AttackSkill, (SkillEffect.ATTACK,)),
        (RecoverySkill, (SkillEffect.HEAL_HP, SkillEffect.HEAL_MP)),
        (BuffSkill, (SkillEffect.BUFF,)),
        (DebuffSkill, (SkillEffect.DEBUFF,)),
        (AilmentSkill, (SkillEffect.AILMENT,)),
        (UtilitySkill, (SkillEffect.RECOVER_MOVES, SkillEffect.EQUIP_ATTACK)),
    )
    for effect in effects
}
LEGACY_SKILL_FIELDS = {
    "multiplier",
    "amount_mode",
    "stat",
    "duration_turns",
    "stack_limit",
    "ailment",
}


def migrate_skill(value):
    """旧保存・戦闘スナップショットの無関係な既知項目だけを取り除く."""
    if isinstance(value, SkillBase):
        value = value.model_dump()
    if not isinstance(value, dict):
        return value
    effect = value.get("effect")
    if not isinstance(effect, str) or not (model := SKILL_MODELS.get(effect)):
        return value
    obsolete = LEGACY_SKILL_FIELDS - model.model_fields.keys()
    return {key: item for key, item in value.items() if key not in obsolete}


StoredSkill = Annotated[SkillDefinition, BeforeValidator(migrate_skill)]
skill_adapter = TypeAdapter(StoredSkill)


def parse_skill(value) -> SkillDefinition:
    """保存済みの形式も受け付け、種類別の定義として復元する."""
    return skill_adapter.validate_python(value)


class PlayerSkillUse(BaseModel):
    """プレイヤーに公開するスキル。効果定義には使用権限を持たせない."""

    skill_id: DefinitionId
    enabled: bool = True


class SpecialtySkillUse(PlayerSkillUse):
    """専門分野での利用方法。出題条件とPower補正は利用側に属する."""

    quiz_required: bool = True
    quiz_seconds: int = Field(default=30, ge=5, le=300)
    wrong_multiplier: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    power_coefficient: float = Field(default=1, ge=0, le=1000, allow_inf_nan=False)


class ItemDefinition(BaseModel):
    """効果そのものを持たず、スキルを参照するアイテム。."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    icon: IconId = "flask"
    enabled: bool = True
    skill_id: DefinitionId
    kind: Literal["weapon", "consumable"] = "consumable"
    max_uses: int = Field(default=1, ge=1, le=10000)

    @model_validator(mode="before")
    @classmethod
    def legacy_uses(cls, value):
        """旧武器には30回、旧消耗品には1回を設定する."""
        if isinstance(value, dict) and "max_uses" not in value:
            return {**value, "max_uses": 30 if value.get("kind") == "weapon" else 1}
        return value


class ShopListing(BaseModel):
    """販売条件はアイテムの効果・見た目と分離する."""

    item_id: DefinitionId
    price: int = Field(ge=0, le=1000000)
    enabled: bool = True


class EnemyDefinition(BaseModel):
    """ロスターの出題セットとは独立した敵の見た目と攻撃。."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    icon: IconId = "ghost"
    enabled: bool = True
    boss: bool = False
    attack_skill_id: DefinitionId | None = None
    on_hit_skill_id: DefinitionId | None = None


class TriggerDefinition(BaseModel):
    """管理者が公開する条件テンプレート。任意コードは実行しない."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    condition: Literal[
        "always",
        "hp_below",
        "mp_below",
        "enemy_count",
        "buff_present",
        "buff_absent",
        "poisoned",
    ]
    threshold: int = Field(default=30, ge=1, le=100)
    enabled: bool = True


def default_triggers() -> list[TriggerDefinition]:
    """単独条件のみ。AND/ORの式エディタは設けない."""
    return [
        TriggerDefinition(id=key, name=name, condition=key)
        for key, name in (
            ("always", "いつでも"),
            ("hp_below", "HPがn%以下"),
            ("mp_below", "MPがn%以下"),
            ("enemy_count", "敵がn体以上"),
            ("buff_present", "buffあり"),
            ("buff_absent", "buffなし"),
            ("poisoned", "毒状態"),
        )
    ]


class GameCatalog(BaseModel):
    """IDと参照をまとめて検査し、一括保存する定義。."""

    skills: list[StoredSkill] = Field(default_factory=list, max_length=200)
    items: list[ItemDefinition] = Field(default_factory=list, max_length=200)
    shop: list[ShopListing] = Field(default_factory=list, max_length=200)
    enemies: list[EnemyDefinition] = Field(default_factory=list, max_length=100)
    player_skills: list[PlayerSkillUse] = Field(default_factory=list, max_length=200)
    specialty_skills: list[SpecialtySkillUse] = Field(
        default_factory=list,
        max_length=200,
    )
    triggers: list[TriggerDefinition] = Field(
        default_factory=default_triggers,
        max_length=100,
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_shop(cls, value):
        """旧アイテム価格を移行する。明示的な空のショップは維持する."""
        if not isinstance(value, dict) or "shop" in value:
            return value
        return {
            **value,
            "shop": [
                {"item_id": item["id"], "price": item["price"]}
                for item in value.get("items", [])
                if isinstance(item, dict) and item.get("price") is not None
            ],
        }

    @model_validator(mode="before")
    @classmethod
    def migrate_usage(cls, value):
        """旧フラグを利用定義へ一度だけ移す。明示された空配列は維持する."""
        if not isinstance(value, dict):
            return value
        value = dict(value)
        skills = [
            s.model_dump() if isinstance(s, SkillBase) else s
            for s in value.get("skills", [])
        ]
        if "player_skills" not in value:
            value["player_skills"] = [
                {"skill_id": s["id"]}
                for s in skills
                if s.get("castable", True)
                and not s.get("requires_specialty")
                and not s.get("quiz_required")
                and s.get("effect")
                in {"attack", "heal_hp", "heal_mp", "buff", "debuff", "ailment"}
            ]
        if "specialty_skills" not in value:
            value["specialty_skills"] = [
                {
                    "skill_id": s["id"],
                    "quiz_required": s.get("quiz_required")
                    if s.get("quiz_required") is not None
                    else True,
                    **{
                        key: s[key]
                        for key in (
                            "quiz_seconds",
                            "wrong_multiplier",
                            "power_coefficient",
                        )
                        if key in s
                    },
                }
                for s in skills
                if s.get("requires_specialty")
            ]
        usage_fields = {
            "castable",
            "requires_specialty",
            "quiz_required",
            "quiz_seconds",
            "wrong_multiplier",
            "power_coefficient",
        }
        value["skills"] = [
            {key: item for key, item in skill.items() if key not in usage_fields}
            for skill in skills
        ]
        return value

    @model_validator(mode="after")
    def valid_shop(self):
        """ショップには存在するアイテムを一度ずつ登録する."""
        item_ids = {i.id for i in self.items}
        if len({p.item_id for p in self.shop}) != len(self.shop):
            msg = "ショップのアイテムが重複しています。"
            raise ValueError(msg)
        if any(p.item_id not in item_ids for p in self.shop):
            msg = "ショップのアイテムが見つかりません。"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def valid_references(self):
        """削除や種類変更でアイテム・敵を壊さない。."""
        for definitions in (self.skills, self.items, self.enemies, self.triggers):
            if len({d.id for d in definitions}) != len(definitions):
                msg = "定義IDが重複しています。"
                raise ValueError(msg)
        skills = {s.id: s for s in self.skills}
        for uses in (self.player_skills, self.specialty_skills):
            if len({u.skill_id for u in uses}) != len(uses):
                msg_0 = "スキル利用の参照IDが重複しています。"
                raise ValueError(msg_0)
            if any(u.skill_id not in skills for u in uses):
                msg_0 = "利用するスキルが見つかりません。"
                raise ValueError(msg_0)
        for item in self.items:
            skill = skills.get(item.skill_id)
            if not skill or (item.kind == "weapon") != (skill.effect == "equip_attack"):
                msg = f"{item.name}のスキルまたは種類が不正です。"
                raise ValueError(msg)
        self.validate_enemy_skills(skills)
        return self

    def validate_enemy_skills(self, skills: dict) -> None:
        """敵の攻撃と命中時効果の参照を種類まで検証する."""
        for enemy in self.enemies:
            if enemy.on_hit_skill_id and (
                enemy.on_hit_skill_id not in skills
                or skills[enemy.on_hit_skill_id].effect not in {"debuff", "ailment"}
            ):
                msg = "命中時のスキルにはdebuffまたは状態異常を選んでください。"
                raise ValueError(msg)
            if enemy.attack_skill_id and (
                enemy.attack_skill_id not in skills
                or skills[enemy.attack_skill_id].effect != "attack"
            ):
                msg = "敵の攻撃には攻撃スキルを選んでください。"
                raise ValueError(msg)


def default_catalog() -> GameCatalog:
    """既存アイテムのID・価格・効果を維持した初期カタログ。."""
    return GameCatalog(
        skills=[
            AttackSkill(
                id="fire",
                animation="fire",
                name="ファイア",
                icon="flame",
                effect="attack",
                multiplier=1.5,
                mp_cost=3,
            ),
            AttackSkill(
                id="fire_all",
                animation="fire",
                name="全体ファイア",
                icon="flame",
                effect="attack",
                target="all_enemies",
                multiplier=1.3,
                mp_cost=5,
            ),
            RecoverySkill(
                id="heal",
                animation="heal",
                name="ケアル",
                icon="heart",
                effect="heal_hp",
                target="self",
                amount=20,
                mp_cost=3,
            ),
            RecoverySkill(
                id="potion_heal",
                name="HP回復",
                icon="flask",
                effect="heal_hp",
                target="self",
                amount=20,
                animation="heal",
            ),
            RecoverySkill(
                id="ether_heal",
                name="MP回復",
                icon="droplet",
                effect="heal_mp",
                target="self",
                amount=5,
                animation="ripple",
            ),
            UtilitySkill(
                id="recover_steps",
                name="歩数回復",
                icon="footprints",
                effect="recover_moves",
                target="self",
                amount=3,
            ),
            UtilitySkill(
                id="bronze_power",
                name="銅の剣の装備効果",
                icon="sword",
                effect="equip_attack",
                target="self",
                amount=3,
            ),
            UtilitySkill(
                id="steel_power",
                name="鋼の剣の装備効果",
                icon="sword",
                effect="equip_attack",
                target="self",
                amount=8,
            ),
            AttackSkill(
                id="specialty_burst",
                name="専門分野",
                icon="book",
                effect="attack",
                target="all_enemies",
                multiplier=2,
                mp_cost=3,
                animation="lightning",
            ),
        ],
        player_skills=[
            PlayerSkillUse(skill_id=s) for s in ("fire", "fire_all", "heal")
        ],
        specialty_skills=[SpecialtySkillUse(skill_id="specialty_burst")],
        items=[
            ItemDefinition(
                id="bronze_sword",
                name="銅の剣",
                icon="sword",
                skill_id="bronze_power",
                kind="weapon",
            ),
            ItemDefinition(
                id="steel_sword",
                name="鋼の剣",
                icon="sword",
                skill_id="steel_power",
                kind="weapon",
            ),
            ItemDefinition(
                id="healing_potion",
                name="回復薬",
                skill_id="potion_heal",
            ),
            ItemDefinition(
                id="step_potion",
                name="歩数回復薬",
                icon="footprints",
                skill_id="recover_steps",
            ),
            ItemDefinition(
                id="ether",
                name="MP回復薬",
                icon="droplet",
                skill_id="ether_heal",
            ),
        ],
        shop=[
            ShopListing(item_id="bronze_sword", price=100),
            ShopListing(item_id="steel_sword", price=300),
            ShopListing(item_id="healing_potion", price=30),
            ShopListing(item_id="ether", price=50),
        ],
        enemies=[
            EnemyDefinition(id="ghost", name="ゴースト", icon="ghost"),
            EnemyDefinition(id="bug", name="バグ", icon="bug"),
            EnemyDefinition(id="skull", name="スケルトン", icon="skull"),
            EnemyDefinition(id="boss", name="ボス", icon="crown", boss=True),
        ],
    )


async def get_game_catalog() -> GameCatalog:
    """未保存なら組み込み定義を返し、読み取りでは作成しない。."""
    rows, _ = await adb.cypher_query(
        "MATCH (s:AdminSettings {key:'game_catalog'}) RETURN s.config LIMIT 1",
    )
    return GameCatalog.model_validate_json(rows[0][0]) if rows else default_catalog()


async def update_game_catalog(catalog: GameCatalog) -> GameCatalog:
    """既存の報酬・所持品が参照するIDは維持する。."""
    if not {"bronze_sword", "steel_sword", "healing_potion", "step_potion"} <= {
        i.id for i in catalog.items
    }:
        msg = "既存アイテムは削除せず、無効化してください。"
        raise ValueError(msg)
    await adb.cypher_query(
        "MERGE (s:AdminSettings {key:'game_catalog'}) SET s.config=$config",
        {"config": catalog.model_dump_json()},
    )
    return catalog
