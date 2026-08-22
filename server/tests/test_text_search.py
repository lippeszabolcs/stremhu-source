"""Szabadszöveges (katalógus) keresés tesztjei."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx

from app.modules.indexer_definitions.base_indexer_definition import IndexerClient
from app.modules.indexer_definitions.integrations.ncore import NcoreIndexerDefinition
from app.modules.indexers.schemas.internal import IndexerTorrent
from app.modules.stremio.catalogs_service import StremioCatalogsService
from app.modules.stremio.constants import SEARCH_ID
from app.modules.stremio.enums import MediaType
from app.modules.stremio.schemas import ParsedExtra
from app.modules.torrent_source_provider.service import (
    _TEXT_SEARCH_MAX_RESULTS,
    TorrentSourceProviderService,
)
from tests.rss_helpers import RequestRecorder, create_rss_definition, rss_response


def _create_ncore(handler) -> NcoreIndexerDefinition:
    definition = NcoreIndexerDefinition()
    definition._client = IndexerClient(
        definition=definition,
        transport=httpx.MockTransport(handler),
        base_url=definition.url,
        follow_redirects=True,
    )
    return definition


def _ncore_json_response(results: list[dict]) -> httpx.Response:
    return httpx.Response(
        status_code=200,
        json={
            "results": results,
            "total_results": str(len(results)),
            "perpage": "25",
        },
    )


def test_ncore_text_search_uses_name_field():
    recorder = RequestRecorder(
        lambda request: _ncore_json_response(
            [
                {
                    "torrent_id": 111,
                    "download_url": "https://ncore.pro/torrents.php?action=download&id=111",
                    "seeders": "5",
                    "category": "hd_hun",
                    "imdb_id": "",
                },
                {
                    # IMDb nélküli tartalomnál (pl. zene) az nCore False-t ad
                    "torrent_id": 222,
                    "download_url": "https://ncore.pro/torrents.php?action=download&id=222",
                    "seeders": "3",
                    "category": "mp3_hun",
                    "imdb_id": False,
                },
            ]
        )
    )
    definition = _create_ncore(recorder)

    torrents = asyncio.run(definition.find_torrents_by_text("Formula 1"))

    assert len(recorder.requests) == 1
    params = dict(recorder.requests[0].url.params)
    assert params["miben"] == "name"
    assert params["mire"] == "Formula 1"
    assert params["miszerint"] == "seeders"

    assert len(torrents) == 2
    assert torrents[0].torrent_id == "111"
    # IMDb nélküli találat is átmegy (nincs imdb-szűrés a szöveges úton),
    # az üres/False imdb_id None-ra normalizálódik
    assert all(torrent.imdb_id is None for torrent in torrents)


def test_ncore_text_search_single_page_only():
    # 100 összes találat, 25 / oldal → az imdb-út lapozna, a szöveges NEM
    recorder = RequestRecorder(
        lambda request: httpx.Response(
            status_code=200,
            json={
                "results": [
                    {
                        "torrent_id": 1,
                        "download_url": "https://ncore.pro/dl/1",
                        "seeders": "5",
                        "category": "hd_hun",
                    }
                ],
                "total_results": "100",
                "perpage": "25",
            },
        )
    )
    definition = _create_ncore(recorder)

    asyncio.run(definition.find_torrents_by_text("Formula 1"))

    assert len(recorder.requests) == 1


def test_ncore_imdb_search_unchanged():
    recorder = RequestRecorder(
        lambda request: _ncore_json_response(
            [
                {
                    "torrent_id": 42,
                    "download_url": "https://ncore.pro/dl/42",
                    "seeders": "9",
                    "category": "hd_hun",
                    "imdb_id": "tt1375666",
                }
            ]
        )
    )
    definition = _create_ncore(recorder)

    torrents = asyncio.run(definition.find_torrents_by_imdb_id("tt1375666"))

    params = dict(recorder.requests[0].url.params)
    assert params["miben"] == "imdb"
    assert params["mire"] == "tt1375666"
    assert len(torrents) == 1


def test_rss_text_search_skips_cinemeta():
    cinemeta_calls = {"n": 0}

    def cinemeta_handler(request: httpx.Request) -> httpx.Response:
        cinemeta_calls["n"] += 1
        return httpx.Response(status_code=200, json={"meta": {"name": "X"}})

    recorder = RequestRecorder(lambda request: rss_response("nyaa_search.xml"))
    definition = create_rss_definition(recorder, cinemeta_handler=cinemeta_handler)

    torrents = asyncio.run(definition.find_torrents_by_text("initial d"))

    # Szöveges keresésnél nincs IMDb→cím feloldás
    assert cinemeta_calls["n"] == 0
    assert len(recorder.requests) == 1
    assert "initial%20d" in str(recorder.requests[0].url)
    assert torrents
    assert all(torrent.imdb_id is None for torrent in torrents)


def _indexer_torrent(torrent_id: str, seeders: int) -> IndexerTorrent:
    account = SimpleNamespace(indexer_id="ncore")
    return IndexerTorrent.model_construct(
        indexer_account=account,
        torrent_id=torrent_id,
        download_url=f"https://ncore.pro/dl/{torrent_id}",
        imdb_id=None,
        seeders=seeders,
        media_attributes=[],
    )


def test_provider_text_search_caps_results_before_sync():
    torrents = [_indexer_torrent(str(i), seeders=i) for i in range(40)]

    indexers_service = Mock()
    indexers_service.get_torrents_by_text = AsyncMock(return_value=(torrents, []))

    provider = TorrentSourceProviderService(
        indexers_service=indexers_service,
        torrent_files_service=Mock(),
    )

    synced: list[list[IndexerTorrent]] = []

    async def fake_sync(indexer_torrents):
        synced.append(indexer_torrents)
        return [
            SimpleNamespace(indexer_torrent=t, torrent_file=None)
            for t in indexer_torrents
        ]

    provider._sync_torrent_files = fake_sync  # type: ignore[method-assign]

    sources, errors = asyncio.run(provider.find_by_text("formula 1"))

    assert errors == []
    assert len(synced) == 1
    assert len(synced[0]) == _TEXT_SEARCH_MAX_RESULTS
    # A legjobb seederű találatok maradnak, seeder szerint csökkenő sorrendben
    assert synced[0][0].seeders == 39
    assert synced[0][-1].seeders == 40 - _TEXT_SEARCH_MAX_RESULTS
    assert len(sources) == _TEXT_SEARCH_MAX_RESULTS


class _CatalogProviderStub:
    def __init__(self):
        self.text_queries: list[str] = []
        self.torrent_id_queries: list[str] = []

    async def find_by_text(self, query: str):
        self.text_queries.append(query)
        sources = [
            SimpleNamespace(
                indexer_torrent=SimpleNamespace(seeders=seeders),
                torrent_file=SimpleNamespace(
                    indexer_id="ncore",
                    torrent_id=torrent_id,
                    info=SimpleNamespace(name=name),
                ),
            )
            for torrent_id, name, seeders in [
                ("1", "Formula.1.2026.Race", 3),
                ("2", "Formula.1.2026.Quali", 9),
            ]
        ]
        return sources, []

    async def find_by_torrent_id(self, torrent_id: str):
        self.torrent_id_queries.append(torrent_id)
        return [], []


def _create_catalogs_service(provider) -> StremioCatalogsService:
    return StremioCatalogsService(
        torrent_files_service=Mock(),
        torrent_source_provider_service=provider,
    )


def test_catalog_free_text_search_returns_sorted_metas():
    provider = _CatalogProviderStub()
    service = _create_catalogs_service(provider)

    response = asyncio.run(
        service.get_catalog(
            MediaType.MOVIE,
            SEARCH_ID,
            ParsedExtra(search="Formula 1"),
        )
    )

    assert provider.text_queries == ["Formula 1"]
    assert [meta.name for meta in response.metas] == [
        "Formula.1.2026.Quali",
        "Formula.1.2026.Race",
    ]
    assert response.metas[0].id == "stremhu-source:ncore:2"


def test_catalog_torrent_id_fallbacks_to_text_search():
    # "t-rex" nem valódi torrent id → az üres id-találat után szöveges keresés fut
    provider = _CatalogProviderStub()
    service = _create_catalogs_service(provider)

    response = asyncio.run(
        service.get_catalog(
            MediaType.MOVIE,
            SEARCH_ID,
            ParsedExtra(search="t-rex"),
        )
    )

    assert provider.torrent_id_queries == ["rex"]
    assert provider.text_queries == ["t-rex"]
    assert len(response.metas) == 2


def test_torrent_id_stream_includes_audio_files():
    from app.modules.indexer_accounts.models import IndexerAccountModel
    from app.modules.torrent_streams.schemas import TorrentStream
    from tests.helpers import create_torrent_file_info, create_torrent_info

    account = IndexerAccountModel(
        indexer_id="ncore",
        username="user",
        password="pwd",
    )
    indexer_torrent = IndexerTorrent.model_construct(
        indexer_account=account,
        torrent_id="123",
        download_url="https://ncore.pro/dl/123",
        imdb_id=None,
        seeders=5,
        media_attributes=[],
    )

    torrent_file = SimpleNamespace(
        torrent_id="123",
        info_hash="hash",
        info=create_torrent_info(
            name="Tankcsapda - Album",
            files=[
                create_torrent_file_info(
                    path="01 - Dal.mp3", size=100, is_video=False, is_audio=True
                ),
                create_torrent_file_info(
                    path="cover.jpg", size=10, is_video=False, is_audio=False, index=1
                ),
                create_torrent_file_info(
                    path="klip.mkv", size=500, is_video=True, index=2
                ),
            ],
        ),
    )

    user = SimpleNamespace(api_key="key")

    streams = TorrentStream.from_torrent_id(
        indexer_torrent=indexer_torrent,  # type: ignore[arg-type]
        torrent_file=torrent_file,  # type: ignore[arg-type]
        app_url="https://app.test",
        user=user,  # type: ignore[arg-type]
    )

    # A hangfájl és a videó bekerül, a kép nem
    assert [stream.file_name for stream in streams] == ["01 - Dal.mp3", "klip.mkv"]


def test_catalog_short_query_returns_empty():
    provider = _CatalogProviderStub()
    service = _create_catalogs_service(provider)

    response = asyncio.run(
        service.get_catalog(
            MediaType.MOVIE,
            SEARCH_ID,
            ParsedExtra(search="a"),
        )
    )

    assert response.metas == []
    assert provider.text_queries == []
