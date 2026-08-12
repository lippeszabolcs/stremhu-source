"""
Közös segédek az egyéni indexer definíciókhoz (jelenleg az RSS indexerhez).

- CinemetaClient: IMDb azonosító → cím feloldás
- encode_torrent_id / decode_torrent_id: önhordozó, letöltési URL-t kódoló
  torrent azonosító
"""

import base64
import binascii
from urllib.parse import urlparse

import httpx

_CINEMETA_URL = "https://v3-cinemeta.strem.io"


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

        # Csak a sikeres feloldást cache-eljük — a hibás/üres választ (pl. a
        # Cinemeta átmeneti 403 rate-limitje) NEM, hogy legközelebb újrapróbálja
        if title is not None:
            self._cache[imdb_id] = title
        return title

    async def close(self) -> None:
        await self._client.aclose()
