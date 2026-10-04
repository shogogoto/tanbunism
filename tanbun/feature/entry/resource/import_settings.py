"""Resource importの運用設定."""

from neomodel import adb
from pydantic import BaseModel, Field


class ResourceImportSettings(BaseModel, frozen=True):
    """同期importの同時実行制限."""

    max_concurrent_imports: int = Field(ge=1, le=8, default=1)
    max_concurrent_imports_per_user: int = Field(ge=1, le=4, default=1)


async def get_resource_import_settings() -> ResourceImportSettings:
    """import制限を取得し、未作成なら既定値で作る."""
    defaults = ResourceImportSettings()
    rows, _ = await adb.cypher_query(
        """
        MERGE (settings:AdminSettings {key: 'resource_import'})
        ON CREATE SET
            settings.max_concurrent_imports = $max_concurrent_imports,
            settings.max_concurrent_imports_per_user =
                $max_concurrent_imports_per_user
        RETURN settings.max_concurrent_imports,
            settings.max_concurrent_imports_per_user
        """,
        params=defaults.model_dump(),
    )
    return ResourceImportSettings(
        max_concurrent_imports=rows[0][0],
        max_concurrent_imports_per_user=rows[0][1],
    )


async def update_resource_import_settings(
    settings: ResourceImportSettings,
) -> ResourceImportSettings:
    """import制限を保存する."""
    rows, _ = await adb.cypher_query(
        """
        MERGE (current:AdminSettings {key: 'resource_import'})
        SET current.max_concurrent_imports = $max_concurrent_imports,
            current.max_concurrent_imports_per_user =
                $max_concurrent_imports_per_user
        RETURN current.max_concurrent_imports,
            current.max_concurrent_imports_per_user
        """,
        params=settings.model_dump(),
    )
    return ResourceImportSettings(
        max_concurrent_imports=rows[0][0],
        max_concurrent_imports_per_user=rows[0][1],
    )
