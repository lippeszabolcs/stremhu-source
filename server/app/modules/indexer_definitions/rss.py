"""
Beépített, konfiguráció-vezérelt RSS indexer definíció.

A Torznab definícióval ellentétben NEM igényel külön szoftvert (Prowlarr /
Jackett) — közvetlenül egy publikus tracker RSS keresŐ-feedjét hívja
(pl. nyaa.si). A discovery szándékosan nem találja meg (nem az integrations
package-ben van); a példányokat az IndexerDefinitionsService.load_custom_from_db()
hozza létre.
"""

import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse, urlunparse

import httpx
import pydash
from pydantic import BaseModel

from app.modules.indexer_definitions.base_indexer_definition import (
    BaseIndexerDefinition,
)
from app.modules.indexer_definitions.common import (
    CinemetaClient,
    decode_torrent_id,
    encode_torrent_id,
)
from app.modules.indexer_definitions.exceptions import AuthenticationOtherException
from app.modules.indexer_definitions.protocols import IndexerAccountStorage
from app.modules.indexer_definitions.schemas.internal import (
    AuthCredentialError,
    AuthError,
    IndexerDefinitionFindTorrentsResult,
    IndexerDefinitionLogin,
    IndexerDefinitionTorrent,
)

RSS_KIND = "rss"

# A keresett cím helye a keresŐ-URL-ben
QUERY_PLACEHOLDER = "{query}"

# Minden visszaadott találat .torrent fájlját letölti a rendszer a válasz előtt,
# ezért ez szabja meg a válaszidőt. A publikus oldalak (nyaa) a párhuzamos
# letöltéseket korlátozzák, ezért szűk a limit.
_MAX_RESULTS = 10


class RssPreset(BaseModel):
    id: str
    name: str
    url: str


# Előre beállított publikus trackerek (a kliens legördülőjéhez is)
RSS_PRESETS: list[RssPreset] = [
    RssPreset(
        id="nyaa-anime",
        name="Nyaa.si — Anime",
        url="https://nyaa.si/?page=rss&q={query}&c=1_0&f=0",
    ),
    RssPreset(
        id="nyaa-all",
        name="Nyaa.si — Minden",
        url="https://nyaa.si/?page=rss&q={query}&f=0",
    ),
]


def get_preset(preset_id: str) -> RssPreset | None:
    return next((preset for preset in RSS_PRESETS if preset.id == preset_id), None)


class RssConfig(BaseModel):
    id: str
    name: str
    search_url_template: str


class GenericRssIndexerDefinition(BaseIndexerDefinition):
    def __init__(
        self,
        config: RssConfig,
        indexer_account_storage: IndexerAccountStorage | None = None,
        cinemeta_client: CinemetaClient | None = None,
    ):
        # A base __init__ olvassa a self.url-t, ezért a config előbb kell
        self._config = config
        self._cinemeta = cinemeta_client or CinemetaClient()

        super().__init__(indexer_account_storage)

    # --- Tulajdonságok ---

    @property
    def max_concurrent(self) -> int:
        # A publikus oldalak (nyaa) 429-et adnak párhuzamos letöltésre
        return 3

    @property
    def kind(self) -> str:
        return RSS_KIND

    @property
    def id(self) -> str:
        return self._config.id

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def url(self) -> str:
        # A template hoszt-része a base httpx kliens base_url-jéhez (a kérésekhez
        # mindig abszolút URL-t építünk, így ez nem befolyásolja a keresést)
        parsed = urlparse(self._config.search_url_template)
        return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))

    @property
    def login_path(self) -> str:
        return ""

    @property
    def details_path(self) -> str:
        return ""

    @property
    def requires_full_download(self) -> bool:
        return False

    # --- Hitelesítés / validáció ---

    def _detect_authentication_error(self, response: httpx.Response) -> AuthError:
        if response.status_code in (401, 403):
            return AuthCredentialError(
                message=f"A(z) {self.name} elutasította a kérést (HTTP "
                f"{response.status_code})."
            )
        return None

    async def _login(self, credential: IndexerDefinitionLogin) -> httpx.Response:
        # Nincs bejelentkezés; a felvételkor egy teszt-kereséssel validálunk,
        # hogy az URL értelmezhető RSS-t ad
        response = await self._request_feed("test")

        if self._parse_channel(response) is None:
            raise AuthenticationOtherException(
                f"A(z) {self.name} megadott URL nem ad értelmezhető RSS választ. "
                "Ellenőrizd a keresŐ-URL-t!"
            )

        return response

    # --- Keresés ---

    @property
    def supports_text_search(self) -> bool:
        return True

    async def _fetch_torrents_by_text(
        self, query: str, exclude_adult: bool
    ) -> list[IndexerDefinitionTorrent]:
        # Az RSS találatoknál nincs kategória — a felnőtt tartalom szűrése
        # a katalógus-rétegben, a torrent neve alapján történik
        return await self._search_feed(query, imdb_id=None)

    async def _fetch_torrents(
        self, imdb_id: str, page: int | None = None
    ) -> IndexerDefinitionFindTorrentsResult:
        title = await self._cinemeta.get_title(imdb_id)
        if not title:
            self.logger.warning(
                "Nem sikerült címet feloldani a(z) %s azonosítóhoz, "
                "a keresés kihagyva.",
                imdb_id,
            )
            return IndexerDefinitionFindTorrentsResult(torrents=[])

        torrents = await self._search_feed(title, imdb_id=imdb_id)

        return IndexerDefinitionFindTorrentsResult(
            torrents=torrents,
            next_page=None,
        )

    async def _search_feed(
        self, query: str, imdb_id: str | None
    ) -> list[IndexerDefinitionTorrent]:
        response = await self._request_feed(query)
        channel = self._parse_channel(response)
        if channel is None:
            raise Exception(
                f"A(z) {self.name} nem adott értelmezhető RSS választ."
            )

        torrents: list[IndexerDefinitionTorrent] = []
        for item in channel.findall("item"):
            download_url = self._extract_download_url(item)
            if not download_url:
                continue
            # Magnet linkeket nem támogat a StremHU (mindig .torrent letöltés kell)
            if urlparse(download_url).scheme not in ("http", "https"):
                continue

            torrents.append(
                IndexerDefinitionTorrent(
                    torrent_id=encode_torrent_id(download_url),
                    download_url=download_url,
                    # IMDb keresésnél a találatot a lekérdezett azonosítóhoz
                    # rendeljük, különben a base class kiszűrné
                    imdb_id=imdb_id,
                    seeders=self._extract_seeders(item),
                )
            )

        unique_torrents = pydash.uniq_by(
            torrents, lambda torrent: torrent.torrent_id
        )
        unique_torrents.sort(key=lambda torrent: torrent.seeders, reverse=True)

        return unique_torrents[:_MAX_RESULTS]

    async def _fetch_torrent(self, torrent_id: str) -> IndexerDefinitionTorrent | None:
        # Idegen (nem RSS) azonosítók is ideérkeznek — azokra None a válasz
        download_url = decode_torrent_id(torrent_id)
        if download_url is None:
            return None

        return IndexerDefinitionTorrent(
            torrent_id=torrent_id,
            download_url=download_url,
            imdb_id=None,
        )

    async def _fetch_hit_and_run_ids(self) -> list[str]:
        return []

    async def close(self) -> None:
        await self._cinemeta.close()
        await super().close()

    # --- Belső segédek ---

    def build_search_url(self, query: str) -> str:
        return self._config.search_url_template.replace(
            QUERY_PLACEHOLDER, quote(query)
        )

    async def _request_feed(self, query: str) -> httpx.Response:
        # Abszolút URL-t használunk, így a kliens base_url-je nem játszik
        return await self._client.get(self.build_search_url(query))

    def _parse_channel(self, response: httpx.Response) -> ET.Element | None:
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            return None

        if root.tag == "channel":
            return root
        return root.find("channel")

    def _extract_download_url(self, item: ET.Element) -> str | None:
        enclosure = item.find("enclosure")
        if enclosure is not None:
            enclosure_url = enclosure.get("url")
            if enclosure_url:
                return enclosure_url

        return item.findtext("link") or None

    def _extract_seeders(self, item: ET.Element) -> int:
        # Bármely namespace-elt vagy sima "seeders" mező (nyaa:seeders,
        # torznab:attr name="seeders", vagy <seeders>)
        for element in item.iter():
            tag = element.tag.rsplit("}", 1)[-1].lower()
            if tag == "seeders":
                value = (element.text or "").strip()
                if value.lstrip("-").isdigit():
                    return max(int(value), 0)
            if tag == "attr" and element.get("name") == "seeders":
                value = element.get("value", "")
                if value.lstrip("-").isdigit():
                    return max(int(value), 0)
        return 0
