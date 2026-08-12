import asyncio

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.common.database import Base
from app.modules.indexer_definitions.models import IndexerDefinitionModel
from app.modules.indexer_definitions.service import IndexerDefinitionsService
from app.modules.indexer_definitions.torznab import TORZNAB_KIND, TorznabConfig
from app.modules.preferences.constants import PreferenceKey
from app.modules.preferences.models import PreferenceModel


def _create_session() -> Session:
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    session.add(
        PreferenceModel(
            id=PreferenceKey.SITE,
            name="Oldal",
            description="Torrent oldal",
        )
    )
    session.flush()

    return session


def _add_torznab_row(db: Session, indexer_id: str = "torznab-abc123def456") -> None:
    db.add(
        IndexerDefinitionModel(
            id=indexer_id,
            preference_id=PreferenceKey.SITE,
            name="Teszt Torznab",
            url="https://prowlarr.test/1/api",
            details_path="",
            requires_full_download=False,
            disabled=False,
            kind=TORZNAB_KIND,
            config={"search_mode": "text"},
            order=100,
        )
    )
    db.flush()


def test_sync_to_db_preserves_torznab_rows():
    db = _create_session()
    service = IndexerDefinitionsService()

    _add_torznab_row(db)
    # Elavult builtin sor: nincs már hozzá kódból felfedezett definíció
    db.add(
        IndexerDefinitionModel(
            id="oldtracker",
            preference_id=PreferenceKey.SITE,
            name="Régi Tracker",
            url="https://old.example",
            details_path="/details/{torrent_id}",
            requires_full_download=False,
            disabled=False,
            kind="builtin",
        )
    )
    db.flush()

    service.sync_to_db(db)
    db.flush()

    remaining_ids = {row.id for row in db.query(IndexerDefinitionModel).all()}

    assert "torznab-abc123def456" in remaining_ids
    assert "oldtracker" not in remaining_ids
    # A felfedezett builtinek felkerültek
    assert "ncore" in remaining_ids
    assert "bithumen" in remaining_ids

    torznab_row = db.get(IndexerDefinitionModel, "torznab-abc123def456")
    assert torznab_row is not None
    assert torznab_row.kind == TORZNAB_KIND
    assert torznab_row.config == {"search_mode": "text"}


def test_load_custom_from_db_registers_instances():
    db = _create_session()
    service = IndexerDefinitionsService()

    _add_torznab_row(db)

    service.load_custom_from_db(db)

    instance = service.get_by_id("torznab-abc123def456")
    assert instance.kind == TORZNAB_KIND
    assert instance.name == "Teszt Torznab"
    assert instance.url == "https://prowlarr.test/1/api"

    # A get_list-ben is szerepel a builtinek mellett
    listed_ids = {definition.id for definition in service.get_list()}
    assert "torznab-abc123def456" in listed_ids
    assert "ncore" in listed_ids


def test_register_unregister():
    service = IndexerDefinitionsService()

    instance = service.create_torznab_instance(
        TorznabConfig(
            id="torznab-xyz",
            name="Torznab XYZ",
            url="https://prowlarr.test/2/api",
        )
    )
    service.register(instance)

    assert service.get_by_id("torznab-xyz") is instance

    asyncio.run(service.unregister("torznab-xyz"))

    listed_ids = {definition.id for definition in service.get_list()}
    assert "torznab-xyz" not in listed_ids

    # Idempotens: nem létező id-ra sem dob hibát
    asyncio.run(service.unregister("torznab-xyz"))
