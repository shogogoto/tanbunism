"""配合で出題元を失わず、装備とは独立して保持する専門分野."""

from pydantic import BaseModel, Field

from .catalog import DefinitionId


class Specialty(BaseModel):
    """リソース集合と引き継いだスキル一つ。Powerは保存しない."""

    id: DefinitionId
    name: str = Field(min_length=1, max_length=80)
    resource_ids: list[str] = Field(min_length=1, max_length=1000)
    skill_id: DefinitionId
    equipped: bool = False
