"""
Generikus Torznab indexer definíció (Jackett / Prowlarr kompatibilis).

A beépített integrációkkal ellentétben nem osztályonként egy tracker,
hanem felhasználó által felvett, konfiguráció-vezérelt példányok
(egy példány = egy Torznab feed). A discovery szándékosan nem találja
meg (nem az integrations package-ben van) — a példányokat az
IndexerDefinitionsService.load_custom_from_db() hozza létre.
"""

import asyncio
import base64
import binascii
import re
import xml.etree.ElementTree as ET
from enum import Enum
from urllib.parse import parse_qsl, urlparse, urlunparse

import httpx
import pydash
from pydantic import BaseModel

from app.modules.indexer_definitions.base_indexer_definition import (
    BaseIndexerDefinition,
)
from app.modules.indexer_definitions.exceptions import (
    AuthenticationOtherException,
    CredentialsRequiredException,
)
from app.modules.indexer_definitions.protocols import IndexerAccountStorage
from app.modules.indexer_definitions.schemas.internal import (
    AuthCredentialError,
    AuthError,
    IndexerDefinitionFindTorrentsResult,
    IndexerDefinitionLogin,
    IndexerDefinitionTorrent,
)

TORZNAB_KIND = "torznab"

_PAGE_SIZE = 100
_RESULTS_LIMIT = 300
# A válaszban visszaadott találatok maximuma (seeder szerint a legjobbak).
# Minden visszaadott találat .torrent fájlját letölti a rendszer a válasz
# előtt, ezért ez közvetlenül a Stremio válaszidejét szabályozza.
_MAX_RESULTS = 50
_ERROR_SNIFF_LIMIT = 256
# Torznab/Newznab hitelesítési hibakódok: 100 (rossz kulcs), 101 (felfüggesztett
# fiók), 102 (nincs jogosultság)
_AUTH_ERROR_CODES = {"100", "101", "102"}
_ERROR_CODE_RE = re.compile(rb'<error[^>]*\scode="(\d+)"')
_ERROR_DESCRIPTION_RE = re.compile(rb'<error[^>]*\sdescription="([^"]*)"')

_CINEMETA_URL = "https://v3-cinemeta.strem.io"


class TorznabSearchMode(str, Enum):
    AUTO = "auto"
    TEXT = "text"


class TorznabConfig(BaseModel):
    id: str
    name: str
    url: str
    search_mode: TorznabSearchMode = TorznabSearchMode.AUTO


class TorznabCaps(BaseModel):
    search_available: bool = True
    movie_imdb: bool = False
    tv_imdb: bool = False

    @property
    def supports_imdb_search(self) -> bool:
        return self.movie_imdb or self.tv_imdb


def encode_torrent_id(download_url: str) -> str:
    """A letöltési URL-t önhordozó torrent azonosítóvá kódolja."""
    return base64.urlsafe_b64encode(download_url.encode()).decode().rstrip("=")


def decode_torrent_id(torrent_id: str) -> str | None:
    """Visszafejti a torrent azonosítót; idegen/érvénytelen id esetén None."""
    try:
        padded = torrent_id + "=" * (-len(torrent_id) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None

    if urlparse(decoded).scheme not in ("http", "https"):
        return None

    return decoded


class CinemetaClient:
    """IMDB azonosító -> cím feloldás a Stremio Cinemeta API-ján keresztül."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=_CINEMETA_URL,
            timeout=10.0,
            transport=transport,
        )
        self._cache: dict[str, str | None] = {}

    async def get_title(self, imdb_id: str) -> str | None:
        if imdb_id in self._cache:
            return self._cache[imdb_id]

        title: str | None = None
        for media_type in ("movie", "series"):
            try:
                response = await self._client.get(f"/meta/{media_type}/{imdb_id}.json")
                if response.status_code != 200:
                    continue
                meta = response.json().get("meta") or {}
                name = meta.get("name")
                if name:
                    title = str(name)
                    break
            except Exception:
                continue

        self._cache[imdb_id] = title
        return title

    async def close(self) -> None:
        await self._client.aclose()


class TorznabIndexerDefinition(BaseIndexerDefinition):
    def __init__(
        self,
        config: TorznabConfig,
        indexer_account_storage: IndexerAccountStorage | None = None,
        cinemeta_client: CinemetaClient | None = None,
    ):
        # A base __init__ olvassa a self.url-t, ezért a config előbb kell
        self._config = config
        self._caps: TorznabCaps | None = None
        self._api_key: str | None = None
        self._cinemeta = cinemeta_client or CinemetaClient()

        super().__init__(indexer_account_storage)

        if indexer_account_storage:
            try:
                account = indexer_account_storage.get_credentials(config.id)
                if account:
                    self._api_key = account.password
            except Exception as e:
                self.logger.error(
                    "Failed to load API key for %s: %s", config.name, e
                )

    # --- Tulajdonságok ---

    @property
    def max_concurrent(self) -> int:
        # A Torznab proxy (Prowlarr/Jackett) jól bírja a párhuzamos kéréseket,
        # a .torrent letöltések átfutása miatt fontos a magasabb érték
        return 10

    @property
    def kind(self) -> str:
        return TORZNAB_KIND

    @property
    def id(self) -> str:
        return self._config.id

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def url(self) -> str:
        return self._config.url

    @property
    def login_path(self) -> str:
        return ""

    @property
    def details_path(self) -> str:
        return ""

    @property
    def requires_full_download(self) -> bool:
        return False

    # --- Hitelesítés ---

    def _detect_authentication_error(self, response: httpx.Response) -> AuthError:
        if response.status_code in (401, 403):
            return AuthCredentialError(
                message=f"Érvénytelen API kulcs a(z) {self.name} indexerhez."
            )

        # Olcsó sniff: minden válaszra lefut (torrent letöltésekre is)
        head = response.content[:_ERROR_SNIFF_LIMIT].lstrip()
        if not head.startswith(b"<") or b"<error" not in head:
            return None

        code_match = _ERROR_CODE_RE.search(head)
        if not code_match:
            return None

        code = code_match.group(1).decode()
        if code not in _AUTH_ERROR_CODES:
            return None

        description_match = _ERROR_DESCRIPTION_RE.search(head)
        message = (
            description_match.group(1).decode(errors="replace")
            if description_match
            else None
        )
        return AuthCredentialError(
            message=message or f"Érvénytelen API kulcs a(z) {self.name} indexerhez."
        )

    async def _login(self, credential: IndexerDefinitionLogin) -> httpx.Response:
        response = await self._request_feed(
            {"t": "caps"}, api_key=credential.password
        )

        caps = self._parse_caps(response)
        if caps is None:
            raise AuthenticationOtherException(
                f"A(z) {self.name} nem adott értelmezhető Torznab caps választ. "
                "Ellenőrizd a Torznab URL-t!"
            )

        self._caps = caps
        self._api_key = credential.password
        return response

    # --- Keresés ---

    async def _fetch_torrents(
        self, imdb_id: str, page: int | None = None
    ) -> IndexerDefinitionFindTorrentsResult:
        # A lapozást belül kezeljük (akár két kereséstípus fut), ezért a
        # next_page mindig None
        api_key = await self._ensure_api_key()
        caps = await self._ensure_caps(api_key)

        use_imdb_search = (
            self._config.search_mode == TorznabSearchMode.AUTO
            and caps.supports_imdb_search
        )

        torrents: list[IndexerDefinitionTorrent] = []

        if use_imdb_search:
            search_types = []
            if caps.movie_imdb:
                search_types.append("movie")
            if caps.tv_imdb:
                search_types.append("tvsearch")

            for search_type in search_types:
                torrents.extend(
                    await self._search_paginated(
                        api_key,
                        imdb_id,
                        {"t": search_type, "imdbid": imdb_id.removeprefix("tt")},
                    )
                )
        else:
            title = await self._cinemeta.get_title(imdb_id)
            if not title:
                self.logger.warning(
                    "Nem sikerült címet feloldani a(z) %s IMDB azonosítóhoz, "
                    "a keresés kihagyva.",
                    imdb_id,
                )
                return IndexerDefinitionFindTorrentsResult(torrents=[])

            torrents.extend(
                await self._search_paginated(
                    api_key, imdb_id, {"t": "search", "q": title}
                )
            )

        unique_torrents = pydash.uniq_by(
            torrents, lambda torrent: torrent.torrent_id
        )

        # Seeder szerint a legjobb találatokat tartjuk meg — minden visszaadott
        # torrent fájlját letölti a rendszer, így ez szabja meg a válaszidőt
        unique_torrents.sort(key=lambda torrent: torrent.seeders, reverse=True)

        return IndexerDefinitionFindTorrentsResult(
            torrents=unique_torrents[:_MAX_RESULTS],
            next_page=None,
        )

    async def _fetch_torrent(self, torrent_id: str) -> IndexerDefinitionTorrent | None:
        # Idegen (nem Torznab) azonosítók is ideérkeznek — azokra None a válasz
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

    async def _ensure_api_key(self) -> str:
        if self._api_key:
            return self._api_key

        if self._indexer_account_storage:
            account = await asyncio.to_thread(
                self._indexer_account_storage.get_credentials, self.id
            )
            if account and account.password:
                self._api_key = account.password
                return self._api_key

        raise CredentialsRequiredException(
            f"{self.name} API kulcs nincs megadva."
        )

    async def _ensure_caps(self, api_key: str) -> TorznabCaps:
        if self._caps is not None:
            return self._caps

        response = await self._request_feed({"t": "caps"}, api_key=api_key)
        caps = self._parse_caps(response)
        if caps is None:
            raise AuthenticationOtherException(
                f"A(z) {self.name} nem adott értelmezhető Torznab caps választ."
            )

        self._caps = caps
        return caps

    async def _request_feed(
        self, params: dict[str, str], api_key: str
    ) -> httpx.Response:
        # A konfigurált URL-ben lévő query paramétereket megőrizzük;
        # abszolút URL-t használunk, így a kliens base_url-je nem játszik
        parsed = urlparse(self._config.url)
        base_params = dict(parse_qsl(parsed.query))
        base_url = urlunparse(parsed._replace(query=""))

        return await self._client.get(
            base_url,
            params={**base_params, **params, "apikey": api_key},
        )

    async def _search_paginated(
        self,
        api_key: str,
        imdb_id: str,
        params: dict[str, str],
    ) -> list[IndexerDefinitionTorrent]:
        torrents: list[IndexerDefinitionTorrent] = []
        offset = 0

        while len(torrents) < _RESULTS_LIMIT:
            response = await self._request_feed(
                {
                    **params,
                    "limit": str(_PAGE_SIZE),
                    "offset": str(offset),
                },
                api_key=api_key,
            )

            items, total = self._parse_search_response(response, imdb_id)
            torrents.extend(items)

            offset += _PAGE_SIZE
            if len(items) < _PAGE_SIZE:
                break
            if total is not None and offset >= total:
                break

        return torrents

    def _parse_caps(self, response: httpx.Response) -> TorznabCaps | None:
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            return None

        if root.tag != "caps":
            return None

        def imdb_supported(element: ET.Element | None) -> bool:
            if element is None:
                return False
            if element.get("available", "yes").lower() != "yes":
                return False
            supported_params = element.get("supportedParams", "")
            return "imdbid" in {
                param.strip() for param in supported_params.split(",")
            }

        searching = root.find("searching")
        if searching is None:
            return TorznabCaps()

        search_element = searching.find("search")
        search_available = (
            search_element is None
            or search_element.get("available", "yes").lower() == "yes"
        )

        return TorznabCaps(
            search_available=search_available,
            movie_imdb=imdb_supported(searching.find("movie-search")),
            tv_imdb=imdb_supported(searching.find("tv-search")),
        )

    def _parse_search_response(
        self, response: httpx.Response, imdb_id: str
    ) -> tuple[list[IndexerDefinitionTorrent], int | None]:
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as e:
            raise Exception(
                f"A(z) {self.name} nem adott értelmezhető Torznab választ."
            ) from e

        channel = root.find("channel")
        if channel is None:
            return [], None

        total = self._parse_total(channel)

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
                    # A találatot a lekérdezett IMDB azonosítóhoz rendeljük,
                    # különben a base class kiszűrné
                    imdb_id=imdb_id,
                    seeders=self._extract_seeders(item),
                )
            )

        return torrents, total

    def _parse_total(self, channel: ET.Element) -> int | None:
        for element in channel.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "response":
                total_value = element.get("total")
                if total_value and total_value.isdigit():
                    return int(total_value)
                return None
        return None

    def _extract_download_url(self, item: ET.Element) -> str | None:
        enclosure = item.find("enclosure")
        if enclosure is not None:
            enclosure_url = enclosure.get("url")
            if enclosure_url:
                return enclosure_url

        return item.findtext("link") or None

    def _extract_seeders(self, item: ET.Element) -> int:
        for element in item.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "attr" and element.get("name") == "seeders":
                seeders_value = element.get("value", "")
                if seeders_value.lstrip("-").isdigit():
                    return max(int(seeders_value), 0)
        return 0
