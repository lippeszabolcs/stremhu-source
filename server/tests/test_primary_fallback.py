import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

from app.common.schemas.internal import SeriesInfo
from app.modules.indexer_accounts.models import IndexerAccountModel
from app.modules.indexer_definitions.models import IndexerDefinitionModel
from app.modules.indexers.schemas.internal import IndexerSearchScope, IndexerTorrent
from app.modules.torrent_source_provider.service import TorrentSourceProviderService
from app.modules.torrent_streams.service import TorrentStreamsService
from app.modules.users.models import UserModel
from tests.helpers import create_torrent_file_info, create_torrent_info


def _create_user(torrent_seed: int | None = None) -> "UserModel":
    user = SimpleNamespace(
        api_key="test-api-key",
        attribute_exclusions=[],
        torrent_seed=torrent_seed,
        enable_smart_filter=False,
        smart_filter_limit=1,
        smart_filter_grouping_preference_id=None,
        preference_definitions=[],
    )
    return cast("UserModel", user)


def _create_source(
    indexer_id: str,
    torrent_name: str,
    file_names: list[str],
    seeders: int = 10,
) -> SimpleNamespace:
    account = IndexerAccountModel(
        indexer_id=indexer_id,
        username="user",
        password="pwd",
    )
    account.indexer_definition = IndexerDefinitionModel(
        id=indexer_id,
        name=indexer_id,
        url=f"https://{indexer_id}.test",
        details_path="",
    )

    indexer_torrent = IndexerTorrent(
        indexer_account=account,
        torrent_id=f"{indexer_id}-{torrent_name}",
        download_url=f"https://{indexer_id}.test/dl",
        imdb_id="tt1234567",
        seeders=seeders,
    )

    torrent_info = create_torrent_info(
        name=torrent_name,
        files=[
            create_torrent_file_info(path=file_name, size=1000, index=index)
            for index, file_name in enumerate(file_names)
        ],
    )

    torrent_file = SimpleNamespace(
        info=torrent_info,
        torrent_id=indexer_torrent.torrent_id,
        info_hash=f"hash-{indexer_id}",
        indexer_id=indexer_id,
    )

    return SimpleNamespace(indexer_torrent=indexer_torrent, torrent_file=torrent_file)


class _ProviderStub:
    def __init__(self, primary, secondary, primary_errors=None, secondary_errors=None):
        self._primary = primary
        self._secondary = secondary
        self._primary_errors = primary_errors or []
        self._secondary_errors = secondary_errors or []
        self.calls: list[IndexerSearchScope] = []

    async def find_by_imdb_id(self, imdb_id, scope):
        self.calls.append(scope)
        if scope == IndexerSearchScope.PRIMARY:
            return self._primary, self._primary_errors
        return self._secondary, self._secondary_errors


def _create_service(provider: _ProviderStub) -> TorrentStreamsService:
    settings_service = Mock()
    settings_service.get_app_url.return_value = "https://app.test"

    torrents_service = Mock()
    torrents_service.get_torrents.return_value = []

    preferences_service = Mock()
    preferences_service.get_list.return_value = []

    return TorrentStreamsService(
        db=Mock(),
        torrent_source_provider_service=provider,  # type: ignore[arg-type]
        torrents_service=torrents_service,
        settings_service=settings_service,
        preferences_service=preferences_service,
    )


def test_no_fallback_when_primary_has_streams():
    provider = _ProviderStub(
        primary=[_create_source("ncore", "Movie.2020.1080p", ["Movie.2020.1080p.mkv"])],
        secondary=[_create_source("torznab-abc", "Movie.2020.720p", ["Movie.720p.mkv"])],
    )
    service = _create_service(provider)

    streams, errors = asyncio.run(
        service.find_by_imdb(_create_user(), "tt1234567")
    )

    assert provider.calls == [IndexerSearchScope.PRIMARY]
    assert len(streams) == 1
    assert streams[0].indexer_account.indexer_id == "ncore"
    assert errors == []


def test_fallback_when_primary_is_empty():
    provider = _ProviderStub(
        primary=[],
        secondary=[
            _create_source("torznab-abc", "Anime.2023.1080p", ["Anime.1080p.mkv"])
        ],
    )
    service = _create_service(provider)

    streams, _ = asyncio.run(service.find_by_imdb(_create_user(), "tt1234567"))

    assert provider.calls == [
        IndexerSearchScope.PRIMARY,
        IndexerSearchScope.SECONDARY,
    ]
    assert len(streams) == 1
    assert streams[0].indexer_account.indexer_id == "torznab-abc"


def test_fallback_when_user_filters_remove_all_primary_streams():
    # Az elsődleges ad torrentet, de a felhasználó seeder-minimuma kiszűri —
    # a tartaléknak MÉGIS el kell indulnia (szűrés utáni trigger)
    provider = _ProviderStub(
        primary=[
            _create_source(
                "ncore", "Movie.2020.1080p", ["Movie.2020.1080p.mkv"], seeders=1
            )
        ],
        secondary=[
            _create_source(
                "torznab-abc", "Movie.2020.720p", ["Movie.720p.mkv"], seeders=500
            )
        ],
    )
    service = _create_service(provider)

    streams, _ = asyncio.run(
        service.find_by_imdb(_create_user(torrent_seed=100), "tt1234567")
    )

    assert provider.calls == [
        IndexerSearchScope.PRIMARY,
        IndexerSearchScope.SECONDARY,
    ]
    assert len(streams) == 1
    assert streams[0].indexer_account.indexer_id == "torznab-abc"


def test_fallback_when_episode_not_resolvable_from_primary():
    # A sorozatból VAN elsődleges találat, de a kért epizód nincs benne
    provider = _ProviderStub(
        primary=[
            _create_source(
                "ncore",
                "Series.S01.1080p",
                ["Series.S01E01.1080p.mkv", "Series.S01E02.1080p.mkv"],
            )
        ],
        secondary=[
            _create_source(
                "torznab-abc", "Series.S02.1080p", ["Series.S02E01.1080p.mkv"]
            )
        ],
    )
    service = _create_service(provider)

    series = SeriesInfo(season=2, episode=1)
    streams, _ = asyncio.run(
        service.find_by_imdb(_create_user(), "tt1234567", series)
    )

    assert provider.calls == [
        IndexerSearchScope.PRIMARY,
        IndexerSearchScope.SECONDARY,
    ]
    assert len(streams) == 1
    assert streams[0].indexer_account.indexer_id == "torznab-abc"


def test_fallback_merges_primary_sources_and_errors():
    # A tartalék körben az elsődleges források is bekerülnek a listába
    # (pl. sorozatnál részleges epizód-fedés), és a hibák összefésülődnek
    provider = _ProviderStub(
        primary=[
            _create_source(
                "ncore", "Series.S01.1080p", ["Series.S01E01.1080p.mkv"]
            )
        ],
        secondary=[
            _create_source(
                "torznab-abc", "Series.S02.1080p", ["Series.S02E01.1080p.mkv"]
            )
        ],
        primary_errors=["ncore hiba"],
        secondary_errors=["torznab hiba"],
    )
    service = _create_service(provider)

    series = SeriesInfo(season=2, episode=1)
    streams, errors = asyncio.run(
        service.find_by_imdb(_create_user(), "tt1234567", series)
    )

    # Az S01-es elsődleges torrent az S02E01-re nem ad streamet, a tartalék igen
    assert len(streams) == 1
    assert streams[0].indexer_account.indexer_id == "torznab-abc"
    assert errors == ["ncore hiba", "torznab hiba"]


def test_concurrent_scopes_use_distinct_dedup_keys():
    # Két párhuzamos, azonos imdb_id-jű, de eltérő scope-ú keresés nem
    # kaphatja meg egymás (csonka) eredményét
    started_scopes: list[IndexerSearchScope] = []

    class IndexersStub:
        async def get_torrents_by_imdb_id(self, imdb_id, scope):
            started_scopes.append(scope)
            await asyncio.sleep(0.05)
            return [], []

    torrent_files_service = Mock()
    torrent_files_service.find_list.return_value = []

    provider = TorrentSourceProviderService(
        indexers_service=IndexersStub(),  # type: ignore[arg-type]
        torrent_files_service=torrent_files_service,
    )
    provider._touch_isolated = lambda identifiers: None  # type: ignore[method-assign]

    async def run():
        await asyncio.gather(
            provider.find_by_imdb_id("tt1234567", IndexerSearchScope.PRIMARY),
            provider.find_by_imdb_id("tt1234567", IndexerSearchScope.SECONDARY),
        )

    asyncio.run(run())

    assert sorted(scope.value for scope in started_scopes) == [
        "primary",
        "secondary",
    ]
