import asyncio
import time
from collections.abc import Callable

import libtorrent as libtorrent
from fastapi import HTTPException

from app.common.logger import logger
from app.common.torrent_info import parse_torrent_info
from app.config import config
from app.modules.relay.entities import File, Stream, Torrent
from app.modules.relay.schemas import (
    RelaySettingsUpdate,
    RelayTorrent,
)

# Hiányzó fájl miatti újraellenőrzés: ennyi másodpercen belül egy torrentre
# legfeljebb egyszer kérünk force_recheck-et.
RECHECK_COOLDOWN_SECONDS = 60

# Szellem-stream takarítás: ilyen gyakran fut a söprés, és ennyi ideje
# adatküldés nélkül álló (nem bontott) streamet is kitakarít.
STREAM_SWEEP_INTERVAL_SECONDS = 15
STALE_STREAM_SECONDS = 300


class RelayService:
    def __init__(
        self,
    ):
        self._libtorrent_session = libtorrent.session()

        alert_mask = (
            libtorrent.alert_category.error
            | libtorrent.alert_category.storage
            | libtorrent.alert_category.status
            | libtorrent.alert_category.piece_progress
        )

        self._libtorrent_session.apply_settings(
            {
                "alert_mask": alert_mask,
                "listen_interfaces": f"0.0.0.0:{config.libtorrent_port},[::]:{config.libtorrent_port}",
                "connections_limit": 300,
                "enable_dht": False,
                "enable_lsd": False,
                "auto_sequential": False,
                "piece_extent_affinity": True,
                "peer_timeout": 60,
                "piece_timeout": 10,
                "request_timeout": 30,
                "unchoke_interval": 10,
                "active_downloads": -1,
                "active_seeds": -1,
                "active_limit": -1,
                "connection_speed": 100,
                "mixed_mode_algorithm": libtorrent.bandwidth_mixed_algo_t.prefer_tcp,
                "unchoke_slots_limit": 16,
                "min_reconnect_time": 5,
                "strict_end_game_mode": False,
                "optimistic_unchoke_interval": 20,
            }
        )

        self._torrent_connections_limit = 20
        self._torrents: dict[libtorrent.sha1_hash, Torrent] = {}
        self._loop = asyncio.get_event_loop()
        self.priority_update_queue: asyncio.Queue[str] = asyncio.Queue()

        def on_alert():
            self._loop.call_soon_threadsafe(self.process_alerts)

        self._libtorrent_session.set_alert_notify(on_alert)

        self._piece_fetch_tasks: dict[tuple[str, int], asyncio.Task[bytes]] = {}
        self._pending_piece_reads: dict[tuple[str, int], asyncio.Future[bytes]] = {}
        self._last_recheck_at: dict[str, float] = {}

        # Event hooks for resume data management (Observer Pattern)
        self.on_save_resume: list[Callable[[str, bytes], None]] = []

    def trigger_priority_update(
        self,
        info_hash: str,
    ) -> None:
        self._loop.call_soon_threadsafe(
            self.priority_update_queue.put_nowait, info_hash
        )

    async def priority_manager_loop(self):
        while True:
            # Várjuk meg az első beérkező kérést
            info_hash = await self.priority_update_queue.get()
            info_hashes = {info_hash}

            # Szedjük ki az összes többit is, ami esetleg várakozik (a set deduplikál)
            while not self.priority_update_queue.empty():
                try:
                    info_hashes.add(self.priority_update_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            for info_hash in info_hashes:
                sha1_hash = self._parse_info_hash(info_hash)
                if torrent := self._torrents.get(sha1_hash):
                    await asyncio.to_thread(torrent.priority_manager)

    def update_settings(
        self,
        payload: RelaySettingsUpdate,
    ):
        apply_settings: libtorrent.settings_pack = {}

        if payload.download_limit is not None:
            apply_settings["download_rate_limit"] = payload.download_limit

        if payload.upload_limit is not None:
            apply_settings["upload_rate_limit"] = payload.upload_limit

        if payload.connections_limit is not None:
            apply_settings["connections_limit"] = payload.connections_limit

        if payload.enable_upnp_and_natpmp is not None:
            apply_settings["enable_upnp"] = payload.enable_upnp_and_natpmp
            apply_settings["enable_natpmp"] = payload.enable_upnp_and_natpmp

        if payload.torrent_connections_limit is not None:
            self._torrent_connections_limit = payload.torrent_connections_limit
            for torrent_handle in self._libtorrent_session.get_torrents():
                if torrent_handle.is_valid():
                    torrent_handle.set_max_connections(self._torrent_connections_limit)

        if payload.port is not None:
            apply_settings["listen_interfaces"] = (
                f"0.0.0.0:{payload.port},[::]:{payload.port}"
            )

        self._libtorrent_session.apply_settings(apply_settings)

    def get_torrents(self) -> list[RelayTorrent]:
        torrent_handlers = self._get_torrents()

        return [
            RelayTorrent.from_libtorrent_handle(torrent_handle)
            for torrent_handle in torrent_handlers
        ]

    def get_active_streams(self) -> list[Stream]:
        streams = []
        for torrent in list(self._torrents.values()):
            for file in list(torrent.files.values()):
                streams.extend(list(file.streams.values()))
        return streams

    def get_torrent_file(self, info_hash: str, file_index: int) -> File:
        sha1_info_hash = self._parse_info_hash(info_hash)

        torrent = self._torrents.get(sha1_info_hash)
        if torrent is None:
            raise HTTPException(404, f'"{info_hash}" torrent nem található.')

        file = torrent.files.get(file_index)
        if file is None:
            raise HTTPException(400, "Érvénytelen fájl index.")

        return file

    async def get_piece_data(
        self,
        torrent_handle: libtorrent.torrent_handle,
        piece_index: int,
    ) -> bytes:
        info_hash = str(torrent_handle.info_hash())
        request_key = (info_hash, piece_index)

        task = self._piece_fetch_tasks.get(request_key)
        if task is None or task.done():
            task = asyncio.create_task(
                self._fetch_piece_with_retry(torrent_handle, piece_index, request_key)
            )
            self._piece_fetch_tasks[request_key] = task

        return await asyncio.shield(task)

    async def _fetch_piece_with_retry(
        self,
        torrent_handle: libtorrent.torrent_handle,
        piece_index: int,
        request_key: tuple[str, int],
    ) -> bytes:
        try:
            for attempt in range(4):
                try:
                    return await self._fetch_piece_once(
                        torrent_handle, piece_index, request_key
                    )
                except FileNotFoundError:
                    # A libtorrent szerint megvan a darab, de a fájl nincs a
                    # lemezen (pl. egy azonos mappanevű másik torrent törlése
                    # vitte el). Újraellenőrzést kérünk, hogy a hiányzó
                    # darabok újra letöltődjenek; a következő kör már a
                    # letöltésre vár (piece_finished → read_piece).
                    self.recheck_after_missing_file(torrent_handle)
                    if attempt < 3:
                        await asyncio.sleep(0.5)
                        continue
                    raise
            raise RuntimeError("Unreachable")
        finally:
            if self._piece_fetch_tasks.get(request_key) is asyncio.current_task():
                self._piece_fetch_tasks.pop(request_key, None)

    async def _fetch_piece_once(
        self,
        torrent_handle: libtorrent.torrent_handle,
        piece_index: int,
        request_key: tuple[str, int],
    ) -> bytes:
        future: asyncio.Future[bytes] = self._loop.create_future()
        self._pending_piece_reads[request_key] = future

        if torrent_handle.have_piece(piece_index):
            torrent_handle.read_piece(piece_index)

        try:
            return await future
        finally:
            if self._pending_piece_reads.get(request_key) is future:
                self._pending_piece_reads.pop(request_key, None)
            if not future.done():
                future.cancel()

    def recheck_after_missing_file(
        self,
        torrent_handle: libtorrent.torrent_handle,
    ) -> bool:
        """Hiányzó fájl esetén force_recheck, torrentenként legfeljebb percenként."""
        info_hash = str(torrent_handle.info_hash())
        now = time.monotonic()
        last = self._last_recheck_at.get(info_hash)
        if last is not None and now - last < RECHECK_COOLDOWN_SECONDS:
            return False

        self._last_recheck_at[info_hash] = now
        logger.warning(
            f"⚠️ A torrent ({info_hash}) fájlja hiányzik a lemezről, újraellenőrzés indul."
        )
        torrent_handle.force_recheck()
        return True

    async def stream_sweeper_loop(self):
        """Kitakarítja a bontott kapcsolatú vagy régóta tétlen streameket.

        Ezek egyébként az aktív listában maradnának, és betöltenék a
        felhasználó egyidejű lejátszási limitjét ("szellem-streamek").
        """
        while True:
            await asyncio.sleep(STREAM_SWEEP_INTERVAL_SECONDS)
            try:
                await self.sweep_streams()
            except Exception:
                logger.exception("Hiba a stream-takarítás közben.")

    async def sweep_streams(self) -> list[Stream]:
        swept: list[Stream] = []
        for stream in self.get_active_streams():
            if stream.is_destroying:
                continue

            client_gone = await stream.is_client_gone()
            stale = stream.idle_seconds > STALE_STREAM_SECONDS

            if client_gone or stale:
                reason = "a kliens bontotta a kapcsolatot" if client_gone else "tétlen"
                logger.warning(
                    f"🧹 Beragadt stream kitakarítása ({reason}): {stream.file.name}"
                )
                await stream.destroy()
                swept.append(stream)

        return swept

    def add_torrent(
        self,
        torrent_bytes: bytes,
        priority: int = 0,
        resume_bytes: bytes | None = None,
    ) -> RelayTorrent:
        save_path = str(config.downloads_dir.absolute())

        try:
            torrent_info = libtorrent.torrent_info(torrent_bytes)
        except Exception:
            raise HTTPException(400, "A torrent nem érvényes.")

        params: libtorrent.add_torrent_params | None = None
        if resume_bytes:
            params = libtorrent.read_resume_data(resume_bytes)

        if params is None:
            params = libtorrent.add_torrent_params()

        params.ti = torrent_info
        params.save_path = save_path
        params.storage_mode = libtorrent.storage_mode_t.storage_mode_sparse

        torrent_handle = self._libtorrent_session.add_torrent(params)
        torrent_handle.set_max_connections(self._torrent_connections_limit)
        torrent_handle.unset_flags(libtorrent.torrent_flags.disable_pex)

        priorities = torrent_handle.piece_priorities()
        torrent_handle.prioritize_pieces([priority] * len(priorities))

        parsed_torrent_info = parse_torrent_info(torrent_info)

        torrent = Torrent(
            torrent_handle=torrent_handle,
            torrent_info=parsed_torrent_info,
            service=self,
            default_priority=priority,
        )

        self._torrents[torrent_info.info_hash()] = torrent

        return RelayTorrent.from_libtorrent_handle(torrent_handle)

    def get_torrent(
        self,
        info_hash: str,
    ) -> RelayTorrent | None:
        sha1_info_hash = self._parse_info_hash(info_hash)
        torrent_handle = self._get_torrent(sha1_info_hash)

        if torrent_handle is None:
            return None

        return RelayTorrent.from_libtorrent_handle(torrent_handle)

    def get_torrent_or_raise(
        self,
        info_hash: str,
    ) -> RelayTorrent:
        relay_torrent = self.get_torrent(
            info_hash=info_hash,
        )

        if relay_torrent is None:
            raise HTTPException(404, f'"{info_hash}" torrent nem található.')

        return relay_torrent

    def delete_torrent(
        self,
        info_hash: str,
    ) -> bool:
        sha1_info_hash = self._parse_info_hash(info_hash)

        return self._delete_torrent(sha1_info_hash)

    def _delete_torrent(self, info_hash: libtorrent.sha1_hash) -> bool:
        torrent_handle = self._get_torrent(info_hash)

        if torrent_handle is None:
            return False

        self._torrents.pop(info_hash, None)

        if self._has_other_torrent_with_same_root(torrent_handle):
            # Ugyanaz a kiadás több trackerről = azonos mappa a lemezen.
            # A fájlok törlése a másik torrent adatát is elvinné, ezért csak
            # a torrentet vesszük ki; a fájlok az utolsó példánnyal törlődnek.
            logger.warning(
                f"⚠️ A törölt torrent ({info_hash}) mappáját másik aktív torrent is használja, a fájlok megmaradnak."
            )
            self._libtorrent_session.remove_torrent(torrent_handle)
        else:
            self._libtorrent_session.remove_torrent(
                torrent_handle,
                libtorrent.options_t.delete_files,
            )

        return True

    @staticmethod
    def _torrent_root_name(torrent_handle: libtorrent.torrent_handle) -> str | None:
        torrent_file = torrent_handle.torrent_file()
        if torrent_file is None:
            return None
        return torrent_file.name()

    def _has_other_torrent_with_same_root(
        self,
        torrent_handle: libtorrent.torrent_handle,
    ) -> bool:
        root_name = self._torrent_root_name(torrent_handle)
        if not root_name:
            return False

        own_info_hash = torrent_handle.info_hash()
        for other in self._libtorrent_session.get_torrents():
            if not other.is_valid() or other.info_hash() == own_info_hash:
                continue
            if self._torrent_root_name(other) == root_name:
                return True
        return False

    def trigger_save_resume_data(self):
        for torrent_handle in self._libtorrent_session.get_torrents():
            if torrent_handle.is_valid():
                torrent_handle.save_resume_data(
                    libtorrent.save_resume_flags_t.flush_disk_cache
                )

    def process_alerts(self):
        alerts = self._libtorrent_session.pop_alerts()

        for alert in alerts:
            try:
                match alert:
                    case libtorrent.save_resume_data_alert():
                        try:
                            resume_data = libtorrent.bencode(
                                libtorrent.write_resume_data(alert.params)
                            )
                            torrent_handle = alert.handle
                            if torrent_handle.is_valid():
                                info_hash_str = str(torrent_handle.info_hash())

                                for callback in self.on_save_resume:
                                    try:
                                        callback(info_hash_str, resume_data)
                                    except Exception as e:
                                        logger.error(
                                            f"Hiba történt az on_save_resume eseménykezelő futtatása közben: {e}"
                                        )
                        except Exception as e:
                            logger.error(
                                f"Hiba történt a torrent visszaállítási adatok mentése közben: {e}"
                            )

                    case libtorrent.save_resume_data_failed_alert():
                        logger.error(
                            f"Hiba történt a torrent visszaállítási adatok mentése közben: {alert.message()}"
                        )

                    case libtorrent.piece_finished_alert():
                        info_hash = str(alert.handle.info_hash())
                        piece_index = alert.piece_index
                        request_key = (info_hash, piece_index)
                        if request_key in self._pending_piece_reads:
                            alert.handle.read_piece(piece_index)
                        self.trigger_priority_update(info_hash)

                    case libtorrent.torrent_checked_alert():
                        # Újraellenőrzés után a meglévő darabokra nem jön
                        # piece_finished, ezért a várakozó olvasásokat itt
                        # indítjuk újra.
                        info_hash = str(alert.handle.info_hash())
                        for pending_hash, piece_index in list(
                            self._pending_piece_reads.keys()
                        ):
                            if pending_hash == info_hash and alert.handle.have_piece(
                                piece_index
                            ):
                                alert.handle.read_piece(piece_index)
                        self.trigger_priority_update(info_hash)

                    case libtorrent.read_piece_alert():
                        info_hash = str(alert.handle.info_hash())
                        request_key = (info_hash, alert.piece)

                        if future := self._pending_piece_reads.pop(request_key, None):
                            has_error = alert.error and alert.error.value() != 0

                            if has_error:
                                error_msg = alert.error.message()
                                error_code = alert.error.value()

                                logger.error(
                                    f"Hiba a libtorrent.read_piece_alert során: {error_msg} (Kód: {error_code})"
                                )

                                if error_code in (2, 3):
                                    err = FileNotFoundError(error_msg)
                                else:
                                    err = Exception(error_msg)

                                if not future.done():
                                    self._loop.call_soon_threadsafe(
                                        future.set_exception, err
                                    )
                            else:
                                piece_data = bytes(alert.buffer)
                                if not future.done():
                                    self._loop.call_soon_threadsafe(
                                        future.set_result, piece_data
                                    )

            except Exception as e:
                logger.error(f"Hiba a {type(alert).__name__} feldolgozása közben: {e}")

    def _parse_info_hash(
        self,
        info_hash_str: str,
    ) -> libtorrent.sha1_hash:
        info_hash = libtorrent.sha1_hash(bytes.fromhex(info_hash_str))
        return info_hash

    def _get_torrents(
        self,
    ) -> list[libtorrent.torrent_handle]:
        torrent_handlers = self._libtorrent_session.get_torrents()

        valid_torrent_handlers = [
            torrent_handler
            for torrent_handler in torrent_handlers
            if torrent_handler.is_valid()
        ]

        return valid_torrent_handlers

    def _get_torrent(
        self,
        info_hash: libtorrent.sha1_hash,
    ) -> libtorrent.torrent_handle | None:
        torrent_handle = self._libtorrent_session.find_torrent(info_hash)

        if not torrent_handle.is_valid():
            return None

        return torrent_handle
