from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.modules.indexer_accounts.schemas import IndexerAccountUpdate
from app.modules.indexer_definitions.rss import QUERY_PLACEHOLDER
from app.modules.indexer_definitions.schemas.api import IndexerDefinitionResponse


class IndexerResponse(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        from_attributes=True,
    )

    indexer_id: str
    indexer_definition: IndexerDefinitionResponse
    username: str
    download_full_torrent: bool
    is_primary: bool
    hit_and_run: bool | None
    keep_seed_seconds: int | None
    updated_at: datetime
    created_at: datetime


class IndexerLoginRequest(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
    )

    indexer_id: str
    username: str
    password: str


class IndexerUpdateRequest(IndexerAccountUpdate):
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
    )


class RssIndexerCreateRequest(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
    )

    name: str
    preset_id: str | None = None
    custom_url: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("A név kitöltése kötelező!")
        return stripped

    @field_validator("custom_url")
    @classmethod
    def validate_custom_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        parsed = urlparse(stripped)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Érvénytelen RSS URL!")
        if QUERY_PLACEHOLDER not in stripped:
            raise ValueError(
                f"A keresŐ-URL-nek tartalmaznia kell a {QUERY_PLACEHOLDER} "
                "helykitöltőt!"
            )
        return stripped

    @model_validator(mode="after")
    def validate_source(self) -> "RssIndexerCreateRequest":
        if not self.preset_id and not self.custom_url:
            raise ValueError("Adj meg egy presetet vagy egy egyéni RSS URL-t!")
        return self
