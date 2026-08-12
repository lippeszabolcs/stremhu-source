from collections.abc import Callable
from pathlib import Path

import httpx

from app.modules.indexer_definitions.base_indexer_definition import IndexerClient
from app.modules.indexer_definitions.torznab import (
    CinemetaClient,
    TorznabConfig,
    TorznabIndexerDefinition,
    TorznabSearchMode,
)

_DATA_DIR = Path(__file__).parent / "data" / "torznab"

TEST_FEED_URL = "https://prowlarr.test/1/api"
TEST_API_KEY = "test-api-key"


def load_fixture(name: str) -> bytes:
    return (_DATA_DIR / name).read_bytes()


def xml_response(fixture_name: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=load_fixture(fixture_name),
        headers={"Content-Type": "application/xml"},
    )


def create_torznab_definition(
    handler: Callable[[httpx.Request], httpx.Response],
    search_mode: TorznabSearchMode = TorznabSearchMode.AUTO,
    cinemeta_handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> TorznabIndexerDefinition:
    """Torznab definíció mockolt HTTP transporttal (nincs valódi hálózat)."""
    cinemeta_client = None
    if cinemeta_handler:
        cinemeta_client = CinemetaClient(
            transport=httpx.MockTransport(cinemeta_handler)
        )

    definition = TorznabIndexerDefinition(
        config=TorznabConfig(
            id="torznab-test123",
            name="Teszt Torznab",
            url=TEST_FEED_URL,
            search_mode=search_mode,
        ),
        cinemeta_client=cinemeta_client,
    )

    definition._client = IndexerClient(
        definition=definition,
        transport=httpx.MockTransport(handler),
        base_url=definition.url,
        follow_redirects=True,
    )

    return definition


class RequestRecorder:
    """Elkapja a mock transport felé menő kéréseket assertekhez."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]):
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)
