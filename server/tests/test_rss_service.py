import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.common.database import Base
from app.modules.indexer_definitions.models import IndexerDefinitionModel
from app.modules.indexer_definitions.rss import RSS_KIND
from app.modules.indexer_definitions.service import IndexerDefinitionsService
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


def _add_rss_row(db: Session, indexer_id: str = "rss-abc123def456") -> None:
    db.add(
        IndexerDefinitionModel(
            id=indexer_id,
            preference_id=PreferenceKey.SITE,
            name="Nyaa RSS",
            url="https://nyaa.si",
            details_path="",
            requires_full_download=False,
            disabled=False,
            kind=RSS_KIND,
            config={"search_url_template": "https://nyaa.si/?page=rss&q={query}&f=0"},
            order=100,
        )
    )
    db.flush()


def test_load_custom_from_db_builds_rss():
    db = _create_session()
    service = IndexerDefinitionsService()

    _add_rss_row(db)
    service.load_custom_from_db(db)

    instance = service.get_by_id("rss-abc123def456")
    assert instance.kind == RSS_KIND
    assert instance.name == "Nyaa RSS"

    listed_ids = {definition.id for definition in service.get_list()}
    assert "rss-abc123def456" in listed_ids
    # A beépítettek is ott vannak
    assert "ncore" in listed_ids


def test_sync_to_db_preserves_rss_rows():
    db = _create_session()
    service = IndexerDefinitionsService()

    _add_rss_row(db)
    service.sync_to_db(db)
    db.flush()

    remaining_ids = {row.id for row in db.query(IndexerDefinitionModel).all()}
    assert "rss-abc123def456" in remaining_ids
    assert "ncore" in remaining_ids

    rss_row = db.get(IndexerDefinitionModel, "rss-abc123def456")
    assert rss_row is not None
    assert rss_row.kind == RSS_KIND
