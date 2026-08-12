import asyncio
from unittest.mock import Mock

from app.modules.indexer_accounts.models import IndexerAccountModel
from app.modules.indexer_definitions.schemas.internal import (
    IndexerDefinitionTorrent,
)
from app.modules.indexers.schemas.internal import IndexerSearchScope
from app.modules.indexers.service import IndexersService


def _create_account(indexer_id: str, is_primary: bool) -> IndexerAccountModel:
    return IndexerAccountModel(
        indexer_id=indexer_id,
        username="user",
        password="pwd",
        is_primary=is_primary,
    )


def _create_service(accounts: list[IndexerAccountModel]) -> IndexersService:
    indexer_accounts_service = Mock()
    indexer_accounts_service.find_list.side_effect = lambda is_primary=None: [
        account
        for account in accounts
        if is_primary is None or account.is_primary == is_primary
    ]

    indexer_definitions_service = Mock()

    def get_by_id(indexer_id: str):
        definition = Mock()

        async def find_torrents_by_imdb_id(imdb_id: str):
            return [
                IndexerDefinitionTorrent(
                    torrent_id=f"{indexer_id}-1",
                    download_url=f"https://{indexer_id}.test/dl/1",
                    imdb_id=imdb_id,
                    seeders=5,
                )
            ]

        definition.find_torrents_by_imdb_id = find_torrents_by_imdb_id
        return definition

    indexer_definitions_service.get_by_id.side_effect = get_by_id

    return IndexersService(
        indexer_definitions_service=indexer_definitions_service,
        indexer_accounts_service=indexer_accounts_service,
        torrents_service=Mock(),
        settings_service=Mock(),
        db=Mock(),
    )


_ACCOUNTS = [
    _create_account("ncore", is_primary=True),
    _create_account("bithumen", is_primary=True),
    _create_account("torznab-abc", is_primary=False),
]


def _searched_indexer_ids(scope: IndexerSearchScope) -> set[str]:
    service = _create_service(_ACCOUNTS)
    torrents, errors = asyncio.run(
        service.get_torrents_by_imdb_id("tt1234567", scope)
    )
    assert errors == []
    return {torrent.indexer_account.indexer_id for torrent in torrents}


def test_primary_scope_only_searches_primary_accounts():
    assert _searched_indexer_ids(IndexerSearchScope.PRIMARY) == {
        "ncore",
        "bithumen",
    }


def test_secondary_scope_only_searches_secondary_accounts():
    assert _searched_indexer_ids(IndexerSearchScope.SECONDARY) == {"torznab-abc"}


def test_all_scope_searches_every_account():
    assert _searched_indexer_ids(IndexerSearchScope.ALL) == {
        "ncore",
        "bithumen",
        "torznab-abc",
    }


def test_default_scope_is_all():
    service = _create_service(_ACCOUNTS)
    torrents, _ = asyncio.run(service.get_torrents_by_imdb_id("tt1234567"))
    assert {torrent.indexer_account.indexer_id for torrent in torrents} == {
        "ncore",
        "bithumen",
        "torznab-abc",
    }
