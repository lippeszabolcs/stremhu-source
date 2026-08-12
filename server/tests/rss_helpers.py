from collections.abc import Callable
from pathlib import Path

import httpx

from app.modules.indexer_definitions.base_indexer_definition import IndexerClient
from app.modules.indexer_definitions.common import CinemetaClient
from app.modules.indexer_definitions.rss import (
    GenericRssIndexerDefinition,
    RssConfig,
)

_DATA_DIR = Path(__file__).parent / "data" / "rss"

TEST_SEARCH_URL = "https://nyaa.si/?page=rss&q={query}&c=1_0&f=0"


def load_fixture(name: str) -> bytes:
    return (_DATA_DIR / name).read_bytes()


def rss_response(fixture_name: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=load_fixture(fixture_name),
        headers={"Content-Type": "application/xml"},
    )


def create_rss_definition(
    handler: Callable[[httpx.Request], httpx.Response],
    cinemeta_handler: Callable[[httpx.Request], httpx.Response] | None = None,
    search_url_template: str = TEST_SEARCH_URL,
) -> GenericRssIndexerDefinition:
    """RSS definíció mockolt HTTP transporttal (nincs valódi hálózat)."""
    cinemeta_client = None
    if cinemeta_handler:
        cinemeta_client = CinemetaClient(
            transport=httpx.MockTransport(cinemeta_handler)
        )

    definition = GenericRssIndexerDefinition(
        config=RssConfig(
            id="rss-test123",
            name="Teszt RSS",
            search_url_template=search_url_template,
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
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]):
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)
