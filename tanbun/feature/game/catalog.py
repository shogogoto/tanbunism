"""管理定義の集約と相互参照の検証・保存."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .catalog_identifiers import DefinitionId, IconId
from .gambit_definitions import (
    MAX_TRIGGER_PERCENT,
    EnemyGambitRule,
    GambitSet,
    TriggerCondition,
    TriggerDefinition,
    TriggerTarget,
    TriggerVariant,
    TriggerVariantField,
    TriggerVariantSet,
    TriggerVariantValue,
    default_triggers,
    supported_trigger,
    trigger_display_name,
    trigger_unlock_id,
)
from .item_categories import ItemCategory
from .rewards import RewardSettings
from .skill_definitions import (
    LEGACY_SKILL_FIELDS,
    SKILL_IDENTITY_FIELDS,
    SKILL_MODELS,
    AilmentEffect,
    AilmentSkill,
    AmountMode,
    AttackEffect,
    AttackSkill,
    BuffEffect,
    BuffSkill,
    CounterResult,
    DebuffEffect,
    DebuffSkill,
    EffectBase,
    EffectDefinition,
    FizzleResult,
    NumericWrongResult,
    RecoveryEffect,
    RecoverySkill,
    ReducedAilmentResult,
    ReducedAttackResult,
    ReducedResult,
    ReducedTimedResult,
    SkillBase,
    SkillDefinition,
    SkillEffect,
    SkillSound,
    SkillStat,
    SkillTarget,
    StatEffect,
    StoredSkill,
    TimedEffect,
    TimedWrongResult,
    UtilityEffect,
    UtilitySkill,
    VariableAmountEffect,
    default_normal_attack,
    migrate_skill,
    migrate_wrong_effects,
    parse_skill,
    skill_adapter,
    validate_effect_combinations,
)
from .skill_templates import (
    MAX_TEMPLATE_ROOT_NAME,
    MAX_VARIANT_DEPTH,
    TEMPLATE_MODELS,
    VARIANT_CLOSE,
    VARIANT_OPEN,
    AilmentTemplate,
    AttackTemplate,
    BuffTemplate,
    DebuffTemplate,
    RecoveryTemplate,
    SkillTemplate,
    TemplateBase,
    TemplateOverride,
    UtilityTemplate,
    default_skill_templates,
    read_effect_templates,
    resolve_skill_templates,
    resolve_template_variants,
    template_effect,
    variant_values,
)
from .template_variant_sets import VariantSet
from .trigger_variants import resolve_trigger_variants

# 既存の利用側はこの窓口を維持し、単体ロジックは専用モジュールを直接参照する。
__all__ = [
    "LEGACY_SKILL_FIELDS",
    "MAX_TEMPLATE_ROOT_NAME",
    "MAX_TRIGGER_PERCENT",
    "MAX_VARIANT_DEPTH",
    "SKILL_IDENTITY_FIELDS",
    "SKILL_MODELS",
    "TEMPLATE_MODELS",
    "VARIANT_CLOSE",
    "VARIANT_OPEN",
    "AilmentEffect",
    "AilmentSkill",
    "AilmentTemplate",
    "AmountMode",
    "AttackEffect",
    "AttackSkill",
    "AttackTemplate",
    "BuffEffect",
    "BuffSkill",
    "BuffTemplate",
    "CounterResult",
    "DebuffEffect",
    "DebuffSkill",
    "DebuffTemplate",
    "DefinitionId",
    "EffectBase",
    "EffectDefinition",
    "ElementDefinition",
    "EnemyDefinition",
    "EnemyGambitRule",
    "EnemyMultipliers",
    "FizzleResult",
    "GambitSet",
    "GambitShopListing",
    "GameCatalog",
    "IconId",
    "ItemDefinition",
    "NumericWrongResult",
    "PlayerSkillUse",
    "RecoveryEffect",
    "RecoverySkill",
    "RecoveryTemplate",
    "ReducedAilmentResult",
    "ReducedAttackResult",
    "ReducedResult",
    "ReducedTimedResult",
    "ShopListing",
    "SkillBase",
    "SkillDefinition",
    "SkillEffect",
    "SkillSound",
    "SkillStat",
    "SkillTarget",
    "SkillTemplate",
    "SpecialtySkillUse",
    "StatEffect",
    "StoredSkill",
    "TemplateBase",
    "TemplateOverride",
    "TimedEffect",
    "TimedWrongResult",
    "TriggerCondition",
    "TriggerDefinition",
    "TriggerTarget",
    "TriggerVariant",
    "TriggerVariantField",
    "TriggerVariantSet",
    "TriggerVariantValue",
    "UtilityEffect",
    "UtilitySkill",
    "UtilityTemplate",
    "VariableAmountEffect",
    "default_catalog",
    "default_elements",
    "default_normal_attack",
    "default_skill_templates",
    "default_triggers",
    "get_game_catalog",
    "migrate_skill",
    "migrate_wrong_effects",
    "parse_skill",
    "read_effect_templates",
    "resolve_skill_templates",
    "resolve_template_variants",
    "skill_adapter",
    "supported_trigger",
    "template_effect",
    "trigger_display_name",
    "trigger_unlock_id",
    "update_game_catalog",
    "validate_effect_combinations",
    "variant_values",
]


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
    category: ItemCategory = ItemCategory.OTHER
    max_uses: int = Field(default=1, ge=1, le=10000)
    equipment_part: Literal["arm", "body", "feet", "head", "accessory"] | None = None

    @model_validator(mode="before")
    @classmethod
    def legacy_uses(cls, value):
        """旧武器には30回、旧消耗品には1回を設定する."""
        if isinstance(value, dict) and "max_uses" not in value:
            return {**value, "max_uses": 30 if value.get("kind") == "weapon" else 1}
        return value


class ShopListing(BaseModel):
    """販売条件はアイテムの効果・見た目と分離する."""

    model_config = ConfigDict(extra="forbid")
    item_id: DefinitionId
    price: int = Field(ge=0, le=1000000)
    enabled: bool = True

    @property
    def product_id(self):
        """通常アイテムの販売対象を区別する。."""
        return self.item_id


class GambitShopListing(BaseModel):
    """ガンビット商品はアイテム効果ではなくトリガーを参照する。."""

    model_config = ConfigDict(extra="forbid")
    trigger_id: DefinitionId
    trigger_variant_id: DefinitionId | None = None
    price: int = Field(ge=0, le=1000000)
    enabled: bool = True

    @property
    def product_id(self):
        """通常アイテムと同じIDでも販売・解放を混同しない。."""
        return f"gambit:{trigger_unlock_id(self.trigger_id, self.trigger_variant_id)}"


class EnemyMultipliers(BaseModel):
    """基礎能力・ボス補正に重ねる敵ごとの個性。未設定は等倍."""

    hp: float = Field(default=1, ge=0.01, le=400, allow_inf_nan=False)
    mp: float = Field(default=1, ge=0.01, le=400, allow_inf_nan=False)
    attack: float = Field(default=1, ge=0.01, le=400, allow_inf_nan=False)
    defense: float = Field(default=1, ge=0.01, le=400, allow_inf_nan=False)
    special_attack: float = Field(default=1, ge=0.01, le=400, allow_inf_nan=False)
    special_defense: float = Field(default=1, ge=0.01, le=400, allow_inf_nan=False)
    speed: float = Field(default=1, ge=0.01, le=400, allow_inf_nan=False)


class EnemyDefinition(BaseModel):
    """ロスターの出題セットとは独立した敵の見た目と攻撃。."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    icon: IconId = "ghost"
    enabled: bool = True
    boss: bool = False
    speed: int | None = Field(default=None, ge=1, le=1000)
    multipliers: EnemyMultipliers = Field(default_factory=EnemyMultipliers)
    attack_skill_id: DefinitionId | None = None
    on_hit_skill_id: DefinitionId | None = None
    gambits: list["EnemyGambitRule"] = Field(default_factory=list, max_length=20)
    gambit_set_id: DefinitionId | None = None
    weaknesses: list[DefinitionId] = Field(default_factory=list, max_length=100)
    resistances: list[DefinitionId] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def distinct_affinities(self):
        """一つの属性は弱点・中立・耐性のいずれか一つだけを持つ."""
        if set(self.weaknesses) & set(self.resistances):
            msg = "同じ属性を弱点と耐性の両方には設定できません。"
            raise ValueError(msg)
        return self


class ElementDefinition(BaseModel):
    """相克表ではなく、表示と参照だけを管理する属性."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=40)
    icon: IconId = "sparkles"
    color: str = Field(default="#94a3b8", pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool = True


def default_elements() -> list[ElementDefinition]:
    """基本八属性。無属性と毒・呪いなどの状態は含めない."""
    return [
        ElementDefinition(id=key, name=name, icon=icon, color=color)
        for key, name, icon, color in [
            ("fire", "炎", "flame", "#ef4444"),
            ("ice", "氷", "snowflake", "#38bdf8"),
            ("lightning", "雷", "zap", "#facc15"),
            ("water", "水", "droplet", "#3b82f6"),
            ("wind", "風", "leaf", "#22c55e"),
            ("earth", "土", "mountain", "#a16207"),
            ("light", "光", "sun", "#fde68a"),
            ("dark", "闇", "moon", "#a78bfa"),
        ]
    ]


class GameCatalog(BaseModel):
    """IDと参照をまとめて検査し、一括保存する定義。."""

    skills: list[StoredSkill] = Field(default_factory=list, max_length=200)
    rewards: RewardSettings | None = None
    boss_multipliers_version: Literal[1] = 1
    variant_sets: list[VariantSet] = Field(default_factory=list, max_length=100)
    trigger_variant_sets: list[TriggerVariantSet] = Field(
        default_factory=list,
        max_length=100,
    )
    elements: list[ElementDefinition] = Field(
        default_factory=default_elements,
        max_length=100,
    )
    skill_templates: list[SkillTemplate] = Field(
        default_factory=default_skill_templates,
        max_length=200,
    )
    items: list[ItemDefinition] = Field(default_factory=list, max_length=200)
    shop: list[ShopListing | GambitShopListing] = Field(
        default_factory=list,
        max_length=300,
    )
    enemies: list[EnemyDefinition] = Field(default_factory=list, max_length=100)
    gambit_sets: list[GambitSet] = Field(default_factory=list, max_length=100)
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
    def apply_trigger_variant_sets(cls, value):
        """共有候補を保存データから解決し、ショップ・戦闘へ同じ定義を渡す."""
        if not isinstance(value, dict):
            return value
        sets = [
            s
            if isinstance(s, TriggerVariantSet)
            else TriggerVariantSet.model_validate(s)
            for s in value.get("trigger_variant_sets", [])
        ]
        if len({s.id for s in sets}) != len(sets):
            msg = "ガンビット変型セットのIDが重複しています。"
            raise ValueError(msg)
        by_id = {s.id: s for s in sets}
        triggers = []
        for raw in value.get("triggers", default_triggers()):
            trigger = (
                raw.model_dump() if isinstance(raw, TriggerDefinition) else dict(raw)
            )
            condition = TriggerCondition.model_validate({
                k: v for k, v in trigger.items() if k in TriggerCondition.model_fields
            })
            trigger = {**condition.model_dump(), "target": None, **trigger}
            triggers.append(resolve_trigger_variants(trigger, by_id))
        return {**value, "triggers": triggers}

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
        items = [
            i.model_dump() if isinstance(i, ItemDefinition) else i
            for i in value.get("items", [])
        ]
        equipment_only = {
            i.get("skill_id")
            for i in items
            if isinstance(i, dict) and i.get("kind") == "weapon"
        }
        if "player_skills" not in value:
            value["player_skills"] = [
                {"skill_id": s["id"]}
                for s in skills
                if s.get("castable", True)
                and s.get("id") != "normal_attack"
                and s.get("id") not in equipment_only
                and not s.get("requires_specialty")
                and not s.get("quiz_required")
                and s.get("effect")
                in {
                    "attack",
                    "heal_hp",
                    "heal_mp",
                    "damage_mp",
                    "buff",
                    "debuff",
                    "ailment",
                }
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
        migrate_wrong_effects(value["skills"], value["specialty_skills"])
        return value

    @model_validator(mode="before")
    @classmethod
    def apply_templates(cls, value):
        """利用設定の旧形式移行より先にテンプレート参照を解決する."""
        return resolve_skill_templates(value)

    def stored_document(self) -> dict:
        """参照スキルは共通値を複製せず、識別情報だけを保存する."""
        document = self.model_dump(mode="json")
        for trigger in document["triggers"]:
            trigger.pop("variant_set_id", None)
            for variant in trigger.get("variants", []):
                for key in ("resolved_name", "resolved_values", "effective_set_ids"):
                    variant.pop(key, None)
        document["skill_templates"] = [
            {
                key: value
                for key, value in template.items()
                if key
                in {
                    "id",
                    "name",
                    "base_template_id",
                    "override_fields",
                    "variant_label",
                    "variant_axis",
                    "variant_set_ids",
                    "variant_selections",
                    *template["override_fields"],
                }
            }
            if template.get("base_template_id")
            else template
            for template in document["skill_templates"]
        ]
        document["skills"] = [
            {key: value for key, value in skill.items() if key in SKILL_IDENTITY_FIELDS}
            if skill.get("template_id") or skill.get("template_ids")
            else skill
            for skill in document["skills"]
        ]
        return document

    @model_validator(mode="after")
    def valid_reward_items(self):
        """報酬表の参照は保存時に検査し、実行時には無効化も反映する。."""
        if self.rewards:
            item_ids = {item.id for item in self.items}
            if any(
                entry.item_id not in item_ids
                for table in self.rewards.tables
                for entry in table.entries
            ):
                msg = "報酬表のアイテムが見つかりません。"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def valid_shop(self):
        """ショップには存在するアイテムを一度ずつ登録する."""
        item_ids = {i.id for i in self.items}
        if len({p.product_id for p in self.shop}) != len(self.shop):
            msg = "ショップのアイテムが重複しています。"
            raise ValueError(msg)
        if any(
            isinstance(p, ShopListing) and p.item_id not in item_ids for p in self.shop
        ):
            msg = "ショップのアイテムが見つかりません。"
            raise ValueError(msg)
        triggers = {t.id: t for t in self.triggers}
        if any(
            isinstance(p, GambitShopListing)
            and (
                p.trigger_id not in triggers
                or not triggers[p.trigger_id].variant(p.trigger_variant_id)
            )
            for p in self.shop
        ):
            msg_0 = "ショップのガンビットが見つかりません。"
            raise ValueError(msg_0)
        return self

    @model_validator(mode="after")
    def valid_elements(self):
        """属性削除でスキル・弱点・状態の参照を壊さない."""
        element_ids = {element.id for element in self.elements}
        if "none" in element_ids:
            msg_0 = "noneは無属性の予約IDです。"
            raise ValueError(msg_0)
        # 以前保存できた毒属性は互換用としてのみ保持する。
        referenced = {skill.element for skill in self.skills} - {"none", "poison"}
        referenced |= {value for enemy in self.enemies for value in enemy.weaknesses}
        referenced |= {value for enemy in self.enemies for value in enemy.resistances}
        for effect in [
            *self.skills,
            *self.skill_templates,
            *(effect for skill in self.skills for effect in skill.effects),
        ]:
            if value := getattr(effect, "affected_element", None):
                referenced.add(value)
        if referenced - element_ids:
            msg_0 = "参照先の属性が見つかりません。"
            raise ValueError(msg_0)
        return self

    @model_validator(mode="after")
    def valid_references(self):
        """削除や種類変更でアイテム・敵を壊さない。."""
        for definitions in (
            self.skills,
            self.items,
            self.enemies,
            self.triggers,
            self.elements,
            self.gambit_sets,
        ):
            if len({d.id for d in definitions}) != len(definitions):
                msg = "定義IDが重複しています。"
                raise ValueError(msg)
        skills = {s.id: s for s in self.skills}
        validate_effect_combinations(self.skills)
        self.validate_trigger_skills(skills)
        normal = skills.get("normal_attack")
        expected_normal = {
            "effect": "attack",
            "damage_type": "physical",
            "target": "enemy",
            "target_count": 1,
            "mp_cost": 0,
            "on_wrong": {"type": "counter"},
            "enabled": True,
        }
        if normal and (
            normal.model_dump(include=set(expected_normal)) != expected_normal
            or len(normal.effects) > 1
        ):
            msg = "戦うは有効な物理攻撃・反撃・敵1体・消費MP0として定義してください。"
            raise ValueError(msg)
        for uses in (self.player_skills, self.specialty_skills):
            if len({u.skill_id for u in uses}) != len(uses):
                msg_0 = "スキル利用の参照IDが重複しています。"
                raise ValueError(msg_0)
            if any(u.skill_id not in skills for u in uses):
                msg_0 = "利用するスキルが見つかりません。"
                raise ValueError(msg_0)
        for item in self.items:
            skill = skills.get(item.skill_id)
            if (
                not skill
                or (
                    item.kind == "weapon"
                    and not all(
                        effect.effect in {"buff", "equip_attack"}
                        for effect in (skill.effects or [skill])
                    )
                )
                or (item.kind != "weapon" and skill.effect == "equip_attack")
            ):
                msg = f"{item.name}のスキルまたは種類が不正です。"
                raise ValueError(msg)
            if "category" not in item.model_fields_set:
                # 旧データだけを補完する。明示したカテゴリは効果から再判定しない。
                item.category = (
                    ItemCategory.WEAPON
                    if item.kind == "weapon"
                    else ItemCategory.RECOVERY
                    if all(
                        effect.effect in {"heal_hp", "heal_mp", "recover_moves"}
                        for effect in (skill.effects or [skill])
                    )
                    else ItemCategory.OTHER
                )
        self.validate_enemy_skills(skills)
        return self

    def validate_trigger_skills(self, skills: dict) -> None:
        """状態の条件に指定したスキルが、継続状態を付与することを確認する。."""
        for shared in self.trigger_variant_sets:
            if shared.field == "state_skill_id":
                for candidate in shared.values:
                    skill = skills.get(candidate.value)
                    if not skill or not any(
                        e.effect in {"buff", "debuff", "ailment"}
                        for e in (skill.effects or [skill])
                    ):
                        msg = "ガンビット変型セットには状態系スキルを選んでください。"
                        raise ValueError(msg)
        for trigger in (
            resolved
            for root in self.triggers
            for resolved in [root, *(root.variant(v.id) for v in root.variants)]
        ):
            if trigger.condition == "state" and trigger.state_skill_id:
                skill = skills.get(trigger.state_skill_id)
                if not skill or not any(
                    effect.effect in {"buff", "debuff", "ailment"}
                    for effect in (skill.effects or [skill])
                ):
                    msg = f"{trigger.name}の状態判定には状態系スキルを選んでください。"
                    raise ValueError(msg)

    def validate_enemy_skills(self, skills: dict) -> None:
        """敵の攻撃と命中時効果の参照を種類まで検証する."""
        set_ids = {group.id for group in self.gambit_sets}
        for enemy in self.enemies:
            if enemy.gambit_set_id and enemy.gambit_set_id not in set_ids:
                msg = "敵のガンビットセットが見つかりません。"
                raise ValueError(msg)
        for rules in [
            *(group.rules for group in self.gambit_sets),
            *(enemy.gambits for enemy in self.enemies),
        ]:
            for rule in rules:
                trigger = next(
                    (t for t in self.triggers if t.id == rule.trigger_id),
                    None,
                )
                skill = skills.get(rule.skill_id)
                if not trigger or not trigger.variant(rule.trigger_variant_id):
                    msg = "敵のガンビットのトリガーまたは変型が見つかりません。"
                    raise ValueError(
                        msg,
                    )
                if not skill or any(
                    e.effect in {"recover_moves", "equip_attack"}
                    for e in (skill.effects or [skill])
                ):
                    msg = "敵のガンビットには戦闘用スキルを選んでください。"
                    raise ValueError(msg)
        for enemy in self.enemies:
            if enemy.on_hit_skill_id and (
                enemy.on_hit_skill_id not in skills
                or any(
                    effect.effect not in {"debuff", "ailment"}
                    for effect in (
                        skills[enemy.on_hit_skill_id].effects
                        or [skills[enemy.on_hit_skill_id]]
                    )
                )
            ):
                msg = "命中時のスキルにはdebuffまたは状態異常を選んでください。"
                raise ValueError(msg)
            if enemy.attack_skill_id and (
                enemy.attack_skill_id not in skills
                or not any(
                    effect.effect in {"attack", "damage_mp"}
                    for effect in (
                        skills[enemy.attack_skill_id].effects
                        or [skills[enemy.attack_skill_id]]
                    )
                )
            ):
                msg = "敵の攻撃には攻撃スキルを選んでください。"
                raise ValueError(msg)


def default_catalog() -> GameCatalog:
    """既存アイテムのID・価格・効果を維持した初期カタログ。."""
    return GameCatalog(
        skills=[
            AttackSkill(
                id="fire",
                element="fire",
                animation="fire",
                name="ファイア",
                icon="flame",
                effect="attack",
                multiplier=1.5,
                mp_cost=3,
            ),
            AttackSkill(
                id="fire_all",
                element="fire",
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
            BuffSkill(
                id="bronze_power",
                name="銅の剣の装備効果",
                icon="sword",
                effect="buff",
                stat="attack",
                target="self",
                amount=3,
            ),
            BuffSkill(
                id="steel_power",
                name="鋼の剣の装備効果",
                icon="sword",
                effect="buff",
                stat="attack",
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
            default_normal_attack(),
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
            EnemyDefinition(
                id="boss",
                name="ボス",
                icon="crown",
                boss=True,
                multipliers=EnemyMultipliers(hp=3, attack=1.25, special_attack=1.25),
            ),
        ],
    )


async def get_game_catalog() -> GameCatalog:
    """既存の呼び出し口を保ち、DB処理は保存モジュールへ委譲する."""
    from . import catalog_repo  # noqa: PLC0415 - DB依存を遅延する

    return await catalog_repo.get_game_catalog()


async def update_game_catalog(catalog: GameCatalog) -> GameCatalog:
    """既存の呼び出し口を保ち、DB処理は保存モジュールへ委譲する."""
    from . import catalog_repo  # noqa: PLC0415 - DB依存を遅延する

    return await catalog_repo.update_game_catalog(catalog)
