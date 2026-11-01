"""Relay: szellem-streamek, hiányzó fájl utáni újraellenőrzés, közös mappa törlése.

Háttér (2026-11-01, otthoni szerver): ugyanaz a kiadás két trackerről azonos
mappába töltődött; az egyik törlése (delete_files) a másik fájljait is
elvitte → a lejátszás "No such file or directory" hibával elhalt → a
megszakított kérések stream-bejegyzései az aktív listában ragadtak →
betelt a felhasználó egyidejű lejátszási limitje → semmi nem indult el.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import libtorrent
import pytest

from app.common.torrent_info import TorrentFileInfo, TorrentInfo
from app.modules.relay.entities import Stream, Torrent
from app.modules.relay.service import RelayService

PIECE_SIZE = 16
FILE_SIZE = 3 * PIECE_SIZE
INFO_HASH = "ab" * 20


def make_torrent(piece_data: bytes = b"x" * PIECE_SIZE) -> Torrent:
    torrent_info = TorrentInfo(
        info_hash=INFO_HASH,
        name="Release.Name",
        size=FILE_SIZE,
        piece_size=PIECE_SIZE,
        files=[
            TorrentFileInfo(
                name="movie.mkv",
                path="Release.Name/movie.mkv",
                index=0,
                size=FILE_SIZE,
                offset=0,
                start_piece_index=0,
                end_piece_index=2,
                is_video=True,
            )
        ],
    )
    handle = Mock()
    handle.have_piece.return_value = True
    handle.piece_priorities.return_value = [0, 0, 0]

    service = Mock()
    service._torrent_connections_limit = 20
    service.trigger_priority_update = Mock()
    service.get_piece_data = AsyncMock(return_value=piece_data)

    return Torrent(
        torrent_handle=handle,
        torrent_info=torrent_info,
        service=service,
        default_priority=0,
    )


def make_request(disconnected: bool = False) -> Mock:
    request = Mock()
    request.is_disconnected = AsyncMock(return_value=disconnected)
    return request


def make_stream(torrent: Torrent, playback_id: str = "p1") -> Stream:
    file = torrent.files[0]
    return Stream(
        stream_id=f"s-{playback_id}",
        playback_id=playback_id,
        user_id="u1",
        torrent=torrent,
        file=file,
        stream_start_byte=0,
        stream_end_byte=FILE_SIZE - 1,
    )


# --- 1. szellem-streamek -----------------------------------------------------


def test_stream_is_not_active_until_the_first_chunk_is_produced():
    async def run():
        torrent = make_torrent()
        file = torrent.files[0]

        generator = await file.stream(
            playback_id="p1",
            user_id="u1",
            stream_start_byte=0,
            stream_end_byte=FILE_SIZE - 1,
            request=make_request(),
        )
        # A kérés megérkezett, de a válasz-generátor még nem indult el:
        # korábban ez már "aktív lejátszásnak" számított.
        assert file.streams == {}

        first_chunk = await generator.__anext__()
        assert first_chunk == b"x" * PIECE_SIZE
        assert len(file.streams) == 1

        async for _ in generator:
            pass
        await asyncio.sleep(0.4)  # destroy() 0.25 s késleltetéssel takarít
        assert file.streams == {}

    asyncio.run(run())


def test_stream_aborted_mid_way_is_removed_from_the_active_list():
    async def run():
        torrent = make_torrent()
        file = torrent.files[0]

        generator = await file.stream(
            playback_id="p1",
            user_id="u1",
            stream_start_byte=0,
            stream_end_byte=FILE_SIZE - 1,
            request=make_request(),
        )
        await generator.__anext__()
        assert len(file.streams) == 1

        await generator.aclose()  # a szerver bontja a választ (kliens elment)
        await asyncio.sleep(0.4)
        assert file.streams == {}

    asyncio.run(run())


def test_stream_failing_on_missing_file_is_removed_from_the_active_list():
    async def run():
        torrent = make_torrent()
        torrent.service.get_piece_data = AsyncMock(
            side_effect=FileNotFoundError("No such file or directory")
        )
        file = torrent.files[0]

        generator = await file.stream(
            playback_id="p1",
            user_id="u1",
            stream_start_byte=0,
            stream_end_byte=FILE_SIZE - 1,
            request=make_request(),
        )
        with pytest.raises(StopAsyncIteration):
            await generator.__anext__()

        await asyncio.sleep(0.4)
        assert file.streams == {}

    asyncio.run(run())


def test_sweeper_removes_disconnected_and_stale_streams_only():
    async def run():
        torrent = make_torrent()
        file = torrent.files[0]

        relay = RelayService.__new__(RelayService)
        relay._torrents = {INFO_HASH: torrent}

        healthy = make_stream(torrent, "healthy")
        healthy.request = make_request(disconnected=False)
        healthy.register()

        gone = make_stream(torrent, "gone")
        gone.request = make_request(disconnected=True)
        gone.register()

        stale = make_stream(torrent, "stale")
        stale.request = make_request(disconnected=False)
        stale.register()
        stale.last_activity_at -= 10_000

        swept = await relay.sweep_streams()
        assert {s.id for s in swept} == {gone.id, stale.id}

        await asyncio.sleep(0.4)
        assert list(file.streams.values()) == [healthy]

    asyncio.run(run())


def test_sweeper_treats_a_broken_request_as_disconnected():
    async def run():
        torrent = make_torrent()
        relay = RelayService.__new__(RelayService)
        relay._torrents = {INFO_HASH: torrent}

        stream = make_stream(torrent)
        stream.request = Mock()
        stream.request.is_disconnected = AsyncMock(side_effect=RuntimeError("closed"))
        stream.register()

        swept = await relay.sweep_streams()
        assert swept == [stream]

    asyncio.run(run())


# --- 2. hiányzó fájl → újraellenőrzés -----------------------------------------


def _bare_relay(loop: asyncio.AbstractEventLoop) -> RelayService:
    relay = RelayService.__new__(RelayService)
    relay._loop = loop
    relay._torrents = {}
    relay._piece_fetch_tasks = {}
    relay._pending_piece_reads = {}
    relay._last_recheck_at = {}
    relay.priority_update_queue = asyncio.Queue()
    return relay


def test_missing_file_triggers_a_recheck_and_the_read_recovers():
    async def run():
        relay = _bare_relay(asyncio.get_running_loop())

        handle = Mock()
        handle.info_hash.return_value = "deadbeef"
        handle.have_piece.return_value = True
        read_calls = {"n": 0}

        def read_piece(piece_index: int) -> None:
            future = relay._pending_piece_reads[("deadbeef", piece_index)]
            read_calls["n"] += 1
            if read_calls["n"] == 1:
                relay._loop.call_soon(
                    future.set_exception, FileNotFoundError("No such file")
                )
            else:
                relay._loop.call_soon(future.set_result, b"piece-data")

        handle.read_piece.side_effect = read_piece

        data = await relay.get_piece_data(handle, 3)

        assert data == b"piece-data"
        handle.force_recheck.assert_called_once()

    asyncio.run(run())


def test_recheck_is_rate_limited_per_torrent():
    relay = RelayService.__new__(RelayService)
    relay._last_recheck_at = {}

    handle = Mock()
    handle.info_hash.return_value = "deadbeef"

    assert relay.recheck_after_missing_file(handle) is True
    assert relay.recheck_after_missing_file(handle) is False
    handle.force_recheck.assert_called_once()

    other = Mock()
    other.info_hash.return_value = "feedface"
    assert relay.recheck_after_missing_file(other) is True


def test_torrent_checked_alert_retriggers_pending_reads_of_that_torrent():
    async def run():
        relay = _bare_relay(asyncio.get_running_loop())
        relay._libtorrent_session = Mock()

        handle = Mock()
        handle.info_hash.return_value = "deadbeef"
        handle.have_piece.return_value = True

        relay._pending_piece_reads = {
            ("deadbeef", 7): relay._loop.create_future(),
            ("other000", 1): relay._loop.create_future(),
        }

        alert = Mock(spec=libtorrent.torrent_checked_alert)
        alert.handle = handle
        relay._libtorrent_session.pop_alerts.return_value = [alert]

        relay.process_alerts()

        handle.read_piece.assert_called_once_with(7)

    asyncio.run(run())


# --- 3. közös mappa: törléskor ne vigyük el a másik torrent fájljait ----------


def _handle(info_hash: str, root_name: str) -> Mock:
    handle = Mock()
    handle.is_valid.return_value = True
    handle.info_hash.return_value = info_hash
    torrent_file = Mock()
    torrent_file.name.return_value = root_name
    handle.torrent_file.return_value = torrent_file
    return handle


def test_delete_keeps_files_when_another_torrent_uses_the_same_folder():
    relay = RelayService.__new__(RelayService)
    relay._torrents = {}
    session = Mock()
    relay._libtorrent_session = session

    ncore = _handle("a" * 40, "Stargate.SG-1.S07.1080p.BluRay.x264.DD5.1-BirdL")
    bithumen = _handle("b" * 40, "Stargate.SG-1.S07.1080p.BluRay.x264.DD5.1-BirdL")
    other = _handle("c" * 40, "Dune.2021.1080p")
    session.get_torrents.return_value = [ncore, bithumen, other]

    session.find_torrent.return_value = bithumen
    assert relay._delete_torrent("b" * 40) is True
    session.remove_torrent.assert_called_once_with(bithumen)  # fájlok maradnak

    session.remove_torrent.reset_mock()
    session.find_torrent.return_value = other
    assert relay._delete_torrent("c" * 40) is True
    session.remove_torrent.assert_called_once_with(
        other, libtorrent.options_t.delete_files
    )


def test_delete_removes_files_when_the_folder_is_not_shared():
    relay = RelayService.__new__(RelayService)
    relay._torrents = {}
    session = Mock()
    relay._libtorrent_session = session

    only = _handle("a" * 40, "Solo.Release")
    session.get_torrents.return_value = [only]
    session.find_torrent.return_value = only

    assert relay._delete_torrent("a" * 40) is True
    session.remove_torrent.assert_called_once_with(
        only, libtorrent.options_t.delete_files
    )
