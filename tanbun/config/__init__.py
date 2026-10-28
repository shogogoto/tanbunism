"""file system."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import BaseModel, Field, PlainSerializer
from typing_extensions import TypedDict

from tanbun.config.env import ClientSettings

s = ClientSettings()
_F: Final = s.config_file


class Credential(TypedDict):
    """認証情報."""

    access_token: str
    token_type: str


class LocalConfig(BaseModel):
    """設定ファイル."""

    ANCHOR: Annotated[
        Path | None,
        PlainSerializer(
            lambda value: str(value) if value is not None else None,
            return_type=str | None,
            when_used="json",
        ),
    ] = None

    CREDENTIALS: Credential | None = None
    CREDENTIALS_URL: str | None = None

    UPLOAD_HISTORY: dict[str, tuple[int, int]] = Field(default_factory=dict)

    @classmethod
    def load(cls) -> Self:
        """読み取り."""
        data = json.loads(_F.read_text()) if _F.exists() else {}
        return cls.model_validate(data)

    def save(self) -> None:
        """書き込み."""
        descriptor = os.open(_F, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(self.model_dump_json(indent=2))
