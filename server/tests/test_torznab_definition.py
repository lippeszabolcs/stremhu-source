import asyncio

import httpx
import pytest

from app.modules.indexer_definitions.exceptions import AuthenticationException
from app.modules.indexer_definitions.schemas.internal import IndexerDefinitionLogin
from app.modules.indexer_definitions.torznab import (
    TorznabSearchMode,
    decode_torrent_id,
    encode_torrent_id,
)
from tests.torznab_helpers import (
    TEST_API_KEY,
    RequestRecorder,
    create_torznab_definition,
    xml_response,
)

_LOGIN = IndexerDefinitionLogin(username="apikey", password=TEST_API_KEY)


def _caps_handler(fixture: str = "caps_imdb.xml"):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("t") == "caps":
            return xml_response(fixture)
        return xml_response("search_movie_p2.xml")

    return handler


def test_login_caps_success():
    recorder = RequestRecorder(_caps_handler())
    definition = create_torznab_definition(recorder)

    async def run():
        await definition.login(_LOGIN)

    asyncio.run(run())

    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request.url.params.get("t") == "caps"
    assert request.url.params.get("apikey") == TEST_API_KEY

    assert definition._caps is not None
    assert definition._caps.movie_imdb is True
    assert definition._caps.tv_imdb is True
    assert definition._api_key == TEST_API_KEY


def test_login_invalid_apikey_error_xml():
    definition = create_torznab_definition(lambda _: xml_response("error_100.xml"))

    async def run():
        await definition.login(_LOGIN)

    with pytest.raises(AuthenticationException, match="Invalid API Key"):
        asyncio.run(run())


def test_login_invalid_apikey_http_401():
    definition = create_torznab_definition(
        lambda _: httpx.Response(status_code=401, content=b"Unauthorized")
    )

    async def run():
        await definition.login(_LOGIN)

    with pytest.raises(AuthenticationException):
        asyncio.run(run())


def test_fetch_torrents_imdbid():
    def handler(request: httpx.Request) -> httpx.Response:
        search_type = request.url.params.get("t")
        if search_type == "caps":
            return xml_response("caps_imdb.xml")
        if search_type == "movie":
            return xml_response("search_movie_p1.xml")
        if search_type == "tvsearch":
            return xml_response("search_movie_p2.xml")
        raise AssertionError(f"Váratlan kereséstípus: {search_type}")

    recorder = RequestRecorder(handler)
    definition = create_torznab_definition(recorder)

    async def run():
        await definition.login(_LOGIN)
        return await definition._fetch_torrents("tt1234567")

    result = asyncio.run(run())

    search_requests = [
        request
        for request in recorder.requests
        if request.url.params.get("t") in ("movie", "tvsearch")
    ]
    assert {request.url.params.get("t") for request in search_requests} == {
        "movie",
        "tvsearch",
    }
    # A tt prefixet a Newznab spec szerint le kell vágni
    assert all(
        request.url.params.get("imdbid") == "1234567" for request in search_requests
    )

    # p1: 3 elem, de az egyik duplikált download_url -> dedupe; p2: 1 elem
    assert len(result.torrents) == 3
    assert result.next_page is None
    assert all(torrent.imdb_id == "tt1234567" for torrent in result.torrents)

    seeders_by_url = {
        torrent.download_url: torrent.seeders for torrent in result.torrents
    }
    assert 42 in seeders_by_url.values()
    assert 7 in seeders_by_url.values()


def test_find_torrents_by_imdb_id_end_to_end():
    def handler(request: httpx.Request) -> httpx.Response:
        search_type = request.url.params.get("t")
        if search_type == "caps":
            return xml_response("caps_imdb.xml")
        if search_type == "movie":
            return xml_response("search_movie_p1.xml")
        return xml_response("search_movie_p2.xml")

    definition = create_torznab_definition(handler)

    async def run():
        await definition.login(_LOGIN)
        return await definition.find_torrents_by_imdb_id("tt1234567")

    torrents = asyncio.run(run())

    # A base class imdb_id egyezőség szűrője nem dobhatja el a találatokat
    assert len(torrents) == 3
    assert all(torrent.imdb_id == "tt1234567" for torrent in torrents)


def test_pagination_internal(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "app.modules.indexer_definitions.torznab._PAGE_SIZE",
        3,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        search_type = request.url.params.get("t")
        if search_type == "caps":
            return xml_response("caps_imdb.xml")
        if search_type == "tvsearch":
            # A tv ág üres, hogy csak a movie lapozását mérjük
            return httpx.Response(
                status_code=200,
                content=b'<?xml version="1.0"?><rss><channel></channel></rss>',
            )
        offset = request.url.params.get("offset")
        if offset == "0":
            return xml_response("search_movie_p1.xml")
        return xml_response("search_movie_p2.xml")

    recorder = RequestRecorder(handler)
    definition = create_torznab_definition(recorder)

    async def run():
        await definition.login(_LOGIN)
        return await definition._fetch_torrents("tt1234567")

    result = asyncio.run(run())

    movie_offsets = [
        request.url.params.get("offset")
        for request in recorder.requests
        if request.url.params.get("t") == "movie"
    ]
    # p1 tele van (3 elem = lapméret) -> jön a következő oldal; p2 rövid -> stop
    assert movie_offsets == ["0", "3"]
    assert result.next_page is None
    # 3 (p1) + 1 (p2) elem, dedupe után 3 egyedi download_url
    assert len(result.torrents) == 3


def test_magnet_items_skipped():
    def handler(request: httpx.Request) -> httpx.Response:
        search_type = request.url.params.get("t")
        if search_type == "caps":
            return xml_response("caps_imdb.xml")
        if search_type == "movie":
            return xml_response("search_with_magnet.xml")
        return httpx.Response(
            status_code=200,
            content=b'<?xml version="1.0"?><rss><channel></channel></rss>',
        )

    definition = create_torznab_definition(handler)

    async def run():
        await definition.login(_LOGIN)
        return await definition._fetch_torrents("tt1234567")

    result = asyncio.run(run())

    assert len(result.torrents) == 1
    assert result.torrents[0].seeders == 12
    assert result.torrents[0].download_url.startswith("https://")


def test_text_mode_uses_cinemeta():
    def cinemeta_handler(request: httpx.Request) -> httpx.Response:
        if "/meta/movie/" in request.url.path:
            return httpx.Response(status_code=404, content=b"{}")
        return httpx.Response(
            status_code=200,
            json={"meta": {"name": "Sousou no Frieren"}},
        )

    def handler(request: httpx.Request) -> httpx.Response:
        search_type = request.url.params.get("t")
        if search_type == "caps":
            return xml_response("caps_text_only.xml")
        if search_type == "search":
            return xml_response("search_movie_p2.xml")
        raise AssertionError(f"Váratlan kereséstípus: {search_type}")

    recorder = RequestRecorder(handler)
    definition = create_torznab_definition(
        recorder,
        search_mode=TorznabSearchMode.TEXT,
        cinemeta_handler=cinemeta_handler,
    )

    async def run():
        await definition.login(_LOGIN)
        return await definition._fetch_torrents("tt22248376")

    result = asyncio.run(run())

    text_requests = [
        request
        for request in recorder.requests
        if request.url.params.get("t") == "search"
    ]
    assert len(text_requests) == 1
    # A movie meta 404 -> series fallback adta a címet
    assert text_requests[0].url.params.get("q") == "Sousou no Frieren"

    assert len(result.torrents) == 1
    assert result.torrents[0].imdb_id == "tt22248376"


def test_auto_mode_falls_back_to_text_without_imdb_caps():
    def cinemeta_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"meta": {"name": "Example Movie"}},
        )

    def handler(request: httpx.Request) -> httpx.Response:
        search_type = request.url.params.get("t")
        if search_type == "caps":
            return xml_response("caps_text_only.xml")
        if search_type == "search":
            return xml_response("search_movie_p2.xml")
        raise AssertionError(f"Váratlan kereséstípus: {search_type}")

    definition = create_torznab_definition(
        handler,
        search_mode=TorznabSearchMode.AUTO,
        cinemeta_handler=cinemeta_handler,
    )

    async def run():
        await definition.login(_LOGIN)
        return await definition._fetch_torrents("tt1234567")

    result = asyncio.run(run())
    assert len(result.torrents) == 1


def test_results_sorted_by_seeders_and_capped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "app.modules.indexer_definitions.torznab._MAX_RESULTS",
        2,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        search_type = request.url.params.get("t")
        if search_type == "caps":
            return xml_response("caps_imdb.xml")
        if search_type == "movie":
            return xml_response("search_movie_p1.xml")
        return xml_response("search_movie_p2.xml")

    definition = create_torznab_definition(handler)

    async def run():
        await definition.login(_LOGIN)
        return await definition._fetch_torrents("tt1234567")

    result = asyncio.run(run())

    # dedupe után 3 egyedi (42, 7, 3 seeder) -> cap 2 -> a legjobb kettő,
    # seeder szerint csökkenő sorrendben
    assert [torrent.seeders for torrent in result.torrents] == [42, 7]


def test_torrent_id_roundtrip():
    download_url = (
        "https://prowlarr.test/1/download?apikey=key&link=aaa&file=Example.Movie"
    )
    torrent_id = encode_torrent_id(download_url)

    assert "=" not in torrent_id
    assert decode_torrent_id(torrent_id) == download_url

    definition = create_torznab_definition(_caps_handler())

    async def run():
        return await definition._fetch_torrent(torrent_id)

    torrent = asyncio.run(run())
    assert torrent is not None
    assert torrent.download_url == download_url
    assert torrent.torrent_id == torrent_id


def test_fetch_torrent_foreign_id_returns_none():
    definition = create_torznab_definition(_caps_handler())

    async def run(torrent_id: str):
        return await definition._fetch_torrent(torrent_id)

    # nCore-stílusú numerikus id és random szemét sem dobhat kivételt
    assert asyncio.run(run("123456")) is None
    assert asyncio.run(run("!!!nem-base64!!!")) is None
    # Érvényes base64, de nem URL a tartalma
    assert asyncio.run(run(encode_torrent_id("nem-url-tartalom"))) is None


def test_detect_auth_error_ignores_binary():
    definition = create_torznab_definition(_caps_handler())

    torrent_like = httpx.Response(
        status_code=200,
        content=b"d8:announce44:https://tracker.example/announce13:creation datei1e",
    )
    assert definition._detect_authentication_error(torrent_like) is None

    rss_response = httpx.Response(
        status_code=200,
        content=b'<?xml version="1.0"?><rss><channel></channel></rss>',
    )
    assert definition._detect_authentication_error(rss_response) is None

    non_auth_error = httpx.Response(
        status_code=200,
        content=b'<error code="201" description="Incorrect parameter" />',
    )
    assert definition._detect_authentication_error(non_auth_error) is None


def test_hit_and_run_is_empty():
    definition = create_torznab_definition(_caps_handler())

    async def run():
        return await definition._fetch_hit_and_run_ids()

    assert asyncio.run(run()) == []
