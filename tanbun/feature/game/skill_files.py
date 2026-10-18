"""スキル定義ファイルの検査と、未保存のカタログへの取り込み."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .catalog import GameCatalog, SkillDefinition, migrate_skill


class SkillFile(BaseModel):
    """利用権限や所持品を含まない、共通効果だけの交換形式."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["tanbunism.skills"]
    version: Literal[1, 2, 3]
    skills: list[SkillDefinition] = Field(max_length=200)

    @model_validator(mode="before")
    @classmethod
    def migrate_v1(cls, value):
        """初版の全項目入りファイルだけ、種類に無関係な旧項目を除去する."""
        if (
            isinstance(value, dict)
            and value.get("version") == 1
            and isinstance(value.get("skills"), list)
        ):
            return {**value, "skills": [migrate_skill(s) for s in value["skills"]]}
        return value

    @model_validator(mode="after")
    def unique_ids(self):
        """一つのファイル内で同じIDを二重に定義できない."""
        if len({skill.id for skill in self.skills}) != len(self.skills):
            msg = "スキルIDが重複しています。"
            raise ValueError(msg)
        return self


class SkillFilePreview(BaseModel):
    """保存せずに現在の編集内容へ取り込むための要求."""

    catalog: GameCatalog
    document: SkillFile

    def merged_catalog(self) -> GameCatalog:
        """同じIDは置換し、それ以外は維持する。参照も再検査する."""
        skills = {skill.id: skill for skill in self.catalog.skills}
        skills.update({skill.id: skill for skill in self.document.skills})
        return GameCatalog.model_validate(
            self.catalog.model_dump() | {"skills": list(skills.values())},
        )
