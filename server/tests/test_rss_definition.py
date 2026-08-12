import asyncio

import httpx
import pytest

from app.modules.indexer_definitions.common import (
    CinemetaClient,
    decode_torrent_id,
    encode_torrent_id,
)
from app.modules.indexer_definitions.exceptions import AuthenticationOtherException
from app.modules.indexer_definitions.rss import (
    RSS_KIND,
    GenericRssIndexerDefinition,
    RssConfig,
    get_preset,
)
from app.modules.indexer_definitions.schemas.internal import IndexerDefinitionLogin
from tests.rss_helpers import (
    RequestRecorder,
    create_rss_definition,
    rss_response,
)


def test_cinemeta_does_not_cache_failed_resolution():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # Elsőre 403 (rate-limit), másodszorra siker
        if calls["n"] <= 2:  # movie + series próbálkozás az első hívásban
            return httpx.Response(status_code=403, content=b"")
        return httpx.Response(status_code=200, json={"meta": {"name": "Frieren"}})

    client = CinemetaClient(transport=httpx.MockTransport(handler))

    async def run():
        first = await client.get_title("tt123")
        second = await client.get_title("tt123")
        await client.close()
        return first, second

    first, second = asyncio.run(run())

    # A sikertelen (403) feloldás NEM cache-elődött → az újrapróbálkozás sikerül
    assert first is None
    assert second == "Frieren"


def test_cinemeta_caches_successful_resolution():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status_code=200, json={"meta": {"name": "Bleach"}})

    client = CinemetaClient(transport=httpx.MockTransport(handler))

    async def run():
        a = await client.get_title("tt999")
        b = await client.get_title("tt999")
        await client.close()
        return a, b

    a, b = asyncio.run(run())
    assert a == "Bleach" and b == "Bleach"
    # A sikeres feloldás cache-elődött → csak egyszer hívta a Cinemeta-t
    assert calls["n"] == 1

_LOGIN = IndexerDefinitionLogin(username="rss", password="")


def _cinemeta_ok(name: str = "Example Anime"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json={"meta": {"name": name}})

    return handler


def test_login_valid_rss():
    definition = create_rss_definition(lambda _: rss_response("nyaa_search.xml"))

    async def run():
        await definition.login(_LOGIN)

    asyncio.run(run())  # nem dob


def test_login_invalid_response():
    definition = create_rss_definition(
        lambda _: httpx.Response(status_code=200, content=b"<html>nem rss</html>")
    )

    async def run():
        await definition.login(_LOGIN)

    with pytest.raises(AuthenticationOtherException):
        asyncio.run(run())


def test_fetch_torrents_resolves_title_and_parses():
    recorder = RequestRecorder(lambda _: rss_response("nyaa_search.xml"))
    definition = create_rss_definition(
        recorder, cinemeta_handler=_cinemeta_ok("Example Anime")
    )

    async def run():
        return await definition._fetch_torrents("tt1234567")

    result = asyncio.run(run())

    # A keresŐ-URL a feloldott címet tartalmazza a {query} helyén
    assert any("Example" in str(r.url) for r in recorder.requests)

    # 2 normál item (a 3. magnet, kihagyva)
    assert len(result.torrents) == 2
    assert all(t.download_url.startswith("https://") for t in result.torrents)
    assert all(t.imdb_id == "tt1234567" for t in result.torrents)

    # Seeder szerint rendezve (120 > 45)
    assert result.torrents[0].seeders == 120
    assert result.torrents[1].seeders == 45


def test_magnet_items_skipped():
    definition = create_rss_definition(
        lambda _: rss_response("nyaa_search.xml"),
        cinemeta_handler=_cinemeta_ok(),
    )

    async def run():
        return await definition._fetch_torrents("tt1234567")

    result = asyncio.run(run())
    assert all("magnet" not in t.download_url for t in result.torrents)


def test_fetch_torrents_title_unresolvable_empty():
    def cinemeta_404(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=404, content=b"{}")

    definition = create_rss_definition(
        lambda _: rss_response("nyaa_search.xml"),
        cinemeta_handler=cinemeta_404,
    )

    async def run():
        return await definition._fetch_torrents("tt999")

    result = asyncio.run(run())
    assert result.torrents == []


def test_torrent_id_roundtrip():
    download_url = "https://nyaa.si/download/1000001.torrent"
    torrent_id = encode_torrent_id(download_url)
    assert decode_torrent_id(torrent_id) == download_url

    definition = create_rss_definition(lambda _: rss_response("nyaa_search.xml"))

    async def run():
        return await definition._fetch_torrent(torrent_id)

    torrent = asyncio.run(run())
    assert torrent is not None
    assert torrent.download_url == download_url


def test_fetch_torrent_foreign_id_returns_none():
    definition = create_rss_definition(lambda _: rss_response("nyaa_search.xml"))

    async def run(tid: str):
        return await definition._fetch_torrent(tid)

    assert asyncio.run(run("123456")) is None
    assert asyncio.run(run("!!!nem-base64!!!")) is None


def test_build_search_url_encodes_query():
    definition = GenericRssIndexerDefinition(
        config=RssConfig(
            id="rss-x",
            name="X",
            search_url_template="https://nyaa.si/?page=rss&q={query}&f=0",
        )
    )
    url = definition.build_search_url("Attack on Titan")
    assert "Attack%20on%20Titan" in url or "Attack%20on%20Titan" in url.replace(
        "+", "%20"
    )
    assert "{query}" not in url


def test_kind_and_preset():
    definition = create_rss_definition(lambda _: rss_response("nyaa_search.xml"))
    assert definition.kind == RSS_KIND

    preset = get_preset("nyaa-anime")
    assert preset is not None
    assert preset.url.count("{query}") == 1
    assert get_preset("ismeretlen") is None
