"""管理者が定義するスキル・所持品・敵の素材。戦闘開始時に効果を固定する。."""

from typing import Annotated, Literal

from neomodel import adb
from pydantic import BaseModel, Field, StringConstraints, model_validator

DefinitionId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
IconId = Literal[
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


class SkillDefinition(BaseModel):
    """クイズの正誤で発動する共通の効果。."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    icon: IconId = "sparkles"
    enabled: bool = True
    effect: Literal["attack", "heal_hp", "heal_mp", "recover_moves", "equip_attack"]
    target: Literal["enemy", "all_enemies", "self"] = "enemy"
    amount: int = Field(default=0, ge=0, le=10000)
    multiplier: float = Field(default=1, ge=0, le=10, allow_inf_nan=False)
    mp_cost: int = Field(default=0, ge=0, le=10000)
    requires_specialty: bool = False
    castable: bool = True

    @model_validator(mode="after")
    def valid_target(self):
        """回復・装備は自分だけを対象にする。."""
        if (self.effect == "attack") == (self.target == "self"):
            msg = "攻撃は敵、それ以外は自分を対象にしてください。"
            raise ValueError(msg)
        return self


class ItemDefinition(BaseModel):
    """効果そのものを持たず、スキルを参照するアイテム。."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    icon: IconId = "flask"
    enabled: bool = True
    skill_id: DefinitionId
    kind: Literal["weapon", "consumable"] = "consumable"
    price: int | None = Field(default=None, ge=0, le=1000000)


class EnemyDefinition(BaseModel):
    """ロスターの出題セットとは独立した敵の見た目と攻撃。."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    icon: IconId = "ghost"
    enabled: bool = True
    boss: bool = False
    attack_skill_id: DefinitionId | None = None


class GameCatalog(BaseModel):
    """IDと参照をまとめて検査し、一括保存する定義。."""

    skills: list[SkillDefinition] = Field(default_factory=list, max_length=200)
    items: list[ItemDefinition] = Field(default_factory=list, max_length=200)
    enemies: list[EnemyDefinition] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_references(self):
        """削除や種類変更でアイテム・敵を壊さない。."""
        for definitions in (self.skills, self.items, self.enemies):
            if len({d.id for d in definitions}) != len(definitions):
                msg = "定義IDが重複しています。"
                raise ValueError(msg)
        skills = {s.id: s for s in self.skills}
        for item in self.items:
            skill = skills.get(item.skill_id)
            if not skill or (item.kind == "weapon") != (skill.effect == "equip_attack"):
                msg = f"{item.name}のスキルまたは種類が不正です。"
                raise ValueError(msg)
        for enemy in self.enemies:
            if enemy.attack_skill_id and (
                enemy.attack_skill_id not in skills
                or skills[enemy.attack_skill_id].effect != "attack"
            ):
                msg = "敵の攻撃には攻撃スキルを選んでください。"
                raise ValueError(msg)
        return self


def default_catalog() -> GameCatalog:
    """既存アイテムのID・価格・効果を維持した初期カタログ。."""
    return GameCatalog(
        skills=[
            SkillDefinition(
                id="fire",
                name="ファイア",
                icon="flame",
                effect="attack",
                multiplier=1.5,
                mp_cost=3,
            ),
            SkillDefinition(
                id="fire_all",
                name="全体ファイア",
                icon="flame",
                effect="attack",
                target="all_enemies",
                multiplier=1.3,
                mp_cost=5,
            ),
            SkillDefinition(
                id="heal",
                name="ケアル",
                icon="heart",
                effect="heal_hp",
                target="self",
                amount=20,
                mp_cost=3,
            ),
            SkillDefinition(
                id="potion_heal",
                name="HP回復",
                icon="flask",
                effect="heal_hp",
                target="self",
                amount=20,
                castable=False,
            ),
            SkillDefinition(
                id="ether_heal",
                name="MP回復",
                icon="droplet",
                effect="heal_mp",
                target="self",
                amount=5,
                castable=False,
            ),
            SkillDefinition(
                id="recover_steps",
                name="歩数回復",
                icon="footprints",
                effect="recover_moves",
                target="self",
                amount=3,
            ),
            SkillDefinition(
                id="bronze_power",
                name="銅の剣の装備効果",
                icon="sword",
                effect="equip_attack",
                target="self",
                amount=3,
            ),
            SkillDefinition(
                id="steel_power",
                name="鋼の剣の装備効果",
                icon="sword",
                effect="equip_attack",
                target="self",
                amount=8,
            ),
            SkillDefinition(
                id="specialty_burst",
                name="専門分野",
                icon="book",
                effect="attack",
                target="all_enemies",
                multiplier=2,
                mp_cost=3,
                requires_specialty=True,
            ),
        ],
        items=[
            ItemDefinition(
                id="bronze_sword",
                name="銅の剣",
                icon="sword",
                skill_id="bronze_power",
                kind="weapon",
                price=100,
            ),
            ItemDefinition(
                id="steel_sword",
                name="鋼の剣",
                icon="sword",
                skill_id="steel_power",
                kind="weapon",
                price=300,
            ),
            ItemDefinition(
                id="healing_potion",
                name="回復薬",
                skill_id="potion_heal",
                price=30,
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
                price=50,
            ),
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
