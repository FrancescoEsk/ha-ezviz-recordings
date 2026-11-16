"""Shared state of the integration: cameras, record lists, clip files, automatic download."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from . import media
from .clip import SERIAL_RE, ClipRef, check_day
from .const import (
    AUTO_RETRY_DELAY,
    AUTO_SECOND_PASS_DELAY,
    CLIP_URL,
    CONF_LANGUAGE,
    CONF_MEDIA_DAYS,
    CONF_MIN_BATTERY,
    CONF_REQUIRE_MOUNT,
    CONF_STORAGE_DIR,
    CONF_STORAGE_LIMIT_GB,
    DOMAIN,
    MIN_COMPLETE_RATIO,
    SIGNED_URL_HOURS,
    SOURCE_DOMAIN,
    STILL_RECORDING_MIN,
    SYNC_MIN_INTERVAL,
    THUMB_URL,
    THUMB_WIDTH,
)
from .playback import PlaybackError, download_ps, list_records
from .records import RecordCache, Span, day_bounds, merge

_LOGGER = logging.getLogger(__name__)

URLS = {"mp4": CLIP_URL, "jpg": THUMB_URL}


class Recordings:
    def __init__(self, hass: HomeAssistant, conf: dict) -> None:
        self.hass = hass
        self.root = Path(conf[CONF_STORAGE_DIR])
        self.limit_bytes = int(conf[CONF_STORAGE_LIMIT_GB] * 1024**3)
        self.require_mount: str | None = conf.get(CONF_REQUIRE_MOUNT)
        self.min_battery: int = conf[CONF_MIN_BATTERY]
        self.media_days: int = conf[CONF_MEDIA_DAYS]
        self.language: str | None = conf.get(CONF_LANGUAGE)
        self.cache = RecordCache(hass)
        self._locks: dict[str, asyncio.Lock] = {}
        self._auto_running = False
        self._last_sync = -SYNC_MIN_INTERVAL
        self._retry_unsub = None
        self._wake_last: dict[tuple[str, str], float] = {}
        self._wake_running: set[tuple[str, str]] = set()

    async def async_load(self) -> None:
        await self.cache.async_load()
        await self.hass.async_add_executor_job(self.prune)

    def prune(self) -> None:
        self.cache.prune(lambda serial, day: (self.root / serial / day).is_dir())

    # -- cameras (from the ezviz_rtsp integration) ----------------------------
    def _coordinators(self) -> list:
        return [
            entry.runtime_data
            for entry in self.hass.config_entries.async_entries(SOURCE_DOMAIN)
            if getattr(getattr(entry, "runtime_data", None), "ezviz_client", None) is not None
        ]

    def _camera(self, serial: str) -> dict:
        for coord in self._coordinators():
            data = (coord.data or {}).get(serial)
            if isinstance(data, dict):
                return data
        return {}

    def cameras(self) -> dict[str, str]:
        """serial -> friendly name."""
        return {
            str(serial): data.get("name") or str(serial)
            for coord in self._coordinators()
            for serial, data in (coord.data or {}).items()
            if isinstance(data, dict) and SERIAL_RE.match(str(serial))  # serials end up in paths
        }

    def client(self, serial: str) -> Any:
        coords = self._coordinators()
        for coord in coords:
            if serial in (coord.data or {}):
                return coord.ezviz_client
        if coords:
            return coords[0].ezviz_client
        raise HomeAssistantError(
            "The EZVIZ integration 'ezviz_rtsp' (Ezviz Cloud with Video) is not set up"
        )

    def battery(self, serial: str) -> int | None:
        try:
            return int(self._camera(serial).get("battery_level"))
        except (TypeError, ValueError):
            return None

    def encrypted(self, serial: str) -> bool:
        return bool(self._camera(serial).get("encrypted"))

    def lock(self, serial: str) -> asyncio.Lock:
        return self._locks.setdefault(serial, asyncio.Lock())

    # -- record lists ------------------------------------------------------------
    async def records(self, serial: str, day: str, *, full: bool = False) -> list[Span]:
        """Records of a day: from cache when complete/fresh, else fetched (incrementally)."""
        check_day(serial, day)
        if not full and (hit := self.cache.get(serial, day)) and hit[1]:
            return hit[0]
        async with self.lock(serial):
            hit = None if full else self.cache.get(serial, day)
            if hit and hit[1]:
                return hit[0]
            old = hit[0] if hit else []
            day_start, day_end = day_bounds(day)
            # incremental: from the last known clip (a minute early, it may have grown)
            start = max(day_start, old[-1][0] - timedelta(minutes=1)) if old else day_start
            fetched = datetime.now()
            try:
                new = await self.hass.async_add_executor_job(
                    list_records, self.client(serial), serial, start, day_end
                )
            except PlaybackError as err:
                raise HomeAssistantError(str(err)) from err
            recs = merge(old, new)
            self.cache.put(serial, day, recs, fetched)
            return recs

    def saved_keys(self, serial: str, day: str, ext: str = "mp4") -> set[str]:
        """Keys of the saved clips ("mp4") or thumbnails ("jpg") of a day (blocking)."""
        folder = self.root / serial / day
        if not folder.is_dir():
            return set()
        return {
            f.stem for f in folder.glob(f"*.{ext}")
            if ".part." not in f.name and f.stat().st_size > 0
        }

    # -- clip files ---------------------------------------------------------------
    def signed_url(self, ref: ClipRef, ext: str, refresh_token_id: str | None = None) -> str:
        return async_sign_path(
            self.hass,
            f"{URLS[ext]}/{ref.serial}/{ref.day}/{ref.key}.{ext}",
            timedelta(hours=SIGNED_URL_HOURS),
            refresh_token_id=refresh_token_id,
        )

    async def _exists(self, path: Path) -> bool:
        return await self.hass.async_add_executor_job(media.nonempty, path)

    async def ensure_clip(self, ref: ClipRef) -> Path:
        """The clip's MP4, downloaded first if needed (and its thumbnail made)."""
        target = ref.file(self.root, "mp4")
        if await self._exists(target):
            return target
        if self.encrypted(ref.serial):
            raise HomeAssistantError(
                "Video encryption is on for this camera: turn it off in the EZVIZ app "
                "(encrypted recordings are not supported yet)"
            )
        async with self.lock(ref.serial):
            if not await self._exists(target):
                await self._download(ref, target)
        try:
            await self.ensure_thumb(ref)
        except HomeAssistantError as err:
            _LOGGER.debug("Thumbnail of %s failed: %s", ref, err)
        return target

    async def _download(self, ref: ClipRef, target: Path) -> None:
        begin, end = ref.span
        try:
            ps = await self.hass.async_add_executor_job(
                download_ps, self.client(ref.serial), ref.serial, begin, end
            )
        except PlaybackError as err:
            raise HomeAssistantError(f"Download failed: {err}") from err
        await self.hass.async_add_executor_job(self._make_dir, target.parent)
        await media.remux_to_mp4(self.hass, ps, target)
        got = await media.duration(self.hass, target)
        if got is not None and got < max(1.0, ref.seconds * MIN_COMPLETE_RATIO):
            # truncated transfer: drop it so the next attempt downloads it again
            await self.hass.async_add_executor_job(target.unlink)
            raise HomeAssistantError(
                f"Incomplete clip ({got:.1f} s of {ref.seconds:.0f} s), try again"
            )

    async def ensure_thumb(self, ref: ClipRef) -> Path | None:
        """Small JPEG from the middle of a saved clip; None if the clip isn't saved."""
        thumb = ref.file(self.root, "jpg")
        if await self._exists(thumb):
            return thumb
        clip = ref.file(self.root, "mp4")
        if not await self._exists(clip):
            return None
        real = await media.duration(self.hass, clip)
        length = min(ref.seconds, real) if real is not None else ref.seconds
        await media.thumbnail(self.hass, clip, length / 2, thumb)
        return thumb

    def _small_thumbs(self) -> list[ClipRef]:
        """Clips whose thumbnail is smaller than THUMB_WIDTH and whose MP4 is still saved,
        newest days first (blocking)."""
        refs: list[ClipRef] = []
        if not self.root.is_dir():
            return refs
        for jpg in sorted(self.root.glob("*/*/*.jpg"), reverse=True):
            if ".part" in jpg.name or not jpg.with_suffix(".mp4").exists():
                continue
            width = media.jpeg_width(jpg)
            if width is None or width >= THUMB_WIDTH:
                continue
            try:
                refs.append(ClipRef.parse(jpg.parent.parent.name, jpg.parent.name, jpg.stem))
            except HomeAssistantError:
                continue
        return refs

    async def upgrade_thumbs(self) -> None:
        """Remake old, smaller thumbnails from the saved MP4s (no camera involved)."""
        refs = await self.hass.async_add_executor_job(self._small_thumbs)
        if not refs:
            return
        _LOGGER.info("Remaking %s thumbnails at %s px", len(refs), THUMB_WIDTH)
        done = 0
        for ref in refs:
            jpg = ref.file(self.root, "jpg")
            try:
                await self.hass.async_add_executor_job(jpg.unlink)
                await self.ensure_thumb(ref)
                done += 1
            except (OSError, HomeAssistantError) as err:
                _LOGGER.debug("Thumbnail of %s not remade: %s", ref, err)
        _LOGGER.info("Remade %s of %s thumbnails", done, len(refs))

    def _make_dir(self, folder: Path) -> None:
        """Create a clip folder; never fill the HA disk if the required share is missing."""
        if self.require_mount and not os.path.ismount(self.require_mount):
            raise HomeAssistantError(f"{self.require_mount} is not mounted: clip not saved")
        folder.mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        """Drop half-written files; delete whole oldest days while above the limit (blocking)."""
        if not self.root.is_dir():
            return
        day_sizes: dict[str, int] = {}
        day_files: dict[str, list[Path]] = {}
        for file in self.root.rglob("*.*"):
            try:
                if not file.is_file():
                    continue
                if ".part." in file.name:
                    file.unlink()
                    continue
                day = file.parent.name
                day_sizes[day] = day_sizes.get(day, 0) + file.stat().st_size
                day_files.setdefault(day, []).append(file)
            except OSError:
                pass
        total = sum(day_sizes.values())
        today = datetime.now().strftime("%Y-%m-%d")
        for day in sorted(day_sizes):
            if total <= self.limit_bytes or day >= today:
                break
            for file in day_files[day]:
                try:
                    file.unlink()
                except OSError:
                    pass
            total -= day_sizes[day]
            _LOGGER.info("Storage limit reached: deleted recordings of %s", day)
        for folder in sorted(self.root.rglob("*"), reverse=True):
            if folder.is_dir():
                try:
                    folder.rmdir()  # only succeeds when empty
                except OSError:
                    pass

    # -- automatic download of today's clips ------------------------------------
    @property
    def auto_running(self) -> bool:
        return self._auto_running

    def request_sync(self) -> bool:
        """Download today's new clips in the background, at most every SYNC_MIN_INTERVAL."""
        now = time.monotonic()
        if self._auto_running or now - self._last_sync < SYNC_MIN_INTERVAL:
            return False
        self._last_sync = now
        self.hass.async_create_background_task(self.download_today(), f"{DOMAIN}_sync")
        return True

    def downloading(self, serial: str, day: str) -> bool:
        """True while clips of this camera and day may still be arriving."""
        return self._auto_running or (serial, day) in self._wake_running

    def request_camera_sync(self, serial: str, day: str) -> bool:
        """The camera was just woken to read its list: download that day's missing clips
        while it is awake, at most every SYNC_MIN_INTERVAL per camera and day."""
        key = (serial, day)
        now = time.monotonic()
        if key in self._wake_running or now - self._wake_last.get(key, -SYNC_MIN_INTERVAL) < SYNC_MIN_INTERVAL:
            return False
        self._wake_last = {k: t for k, t in self._wake_last.items() if now - t < SYNC_MIN_INTERVAL}
        self._wake_last[key] = now
        self._wake_running.add(key)

        async def _run() -> None:
            summary: dict[str, dict[str, int]] = {}
            name = self.cameras().get(serial, serial)
            try:
                await self._download_camera(serial, name, day, summary)
                _LOGGER.info("Download of %s on wake-up finished: %s", day, summary)
            finally:
                self._wake_running.discard(key)

        self.hass.async_create_background_task(_run(), f"{DOMAIN}_wake_{serial}_{day}")
        return True

    async def download_today(self, retry: bool = True) -> dict[str, dict[str, int]]:
        """Download every clip of today not saved yet, camera by camera.

        Cameras that fail get a second pass after a minute; if one still fails,
        a single extra run is scheduled AUTO_RETRY_DELAY later (retry=False there).
        """
        if self._auto_running:
            _LOGGER.info("Automatic download already running, skipped")
            return {}
        self._auto_running = True
        summary: dict[str, dict[str, int]] = {}
        try:
            day = datetime.now().strftime("%Y-%m-%d")
            cams = self.cameras()
            failed = list(cams)
            for pause in (0, AUTO_SECOND_PASS_DELAY):  # a camera in deep sleep needs a 2nd knock
                if not failed:
                    break
                await asyncio.sleep(pause)
                failed = [s for s in failed if not await self._download_camera(s, cams[s], day, summary)]
            if failed:
                self._schedule_retry(", ".join(cams[s] for s in failed), retry)
            _LOGGER.info("Automatic download of %s finished: %s", day, summary)
            return summary
        finally:
            self._auto_running = False

    def _schedule_retry(self, names: str, retry: bool) -> None:
        if not retry or self._retry_unsub is not None:
            _LOGGER.warning("Automatic download incomplete for %s", names)
            return
        _LOGGER.warning("Automatic download incomplete for %s, retrying in %s min",
                        names, AUTO_RETRY_DELAY // 60)

        async def _retry(_now: datetime) -> None:
            self._retry_unsub = None
            await self.download_today(retry=False)

        self._retry_unsub = async_call_later(self.hass, AUTO_RETRY_DELAY, _retry)

    async def _download_camera(self, serial: str, name: str, day: str, summary: dict) -> bool:
        """Download the missing clips of one camera; False if anything failed."""
        stats = summary.setdefault(name, {"new": 0, "errors": 0, "already": 0})
        if self.encrypted(serial):
            stats["skipped_encrypted"] = 1
            return True
        level = self.battery(serial)
        if level is not None and level < self.min_battery:
            _LOGGER.info("%s: battery %s%%, automatic download skipped", name, level)
            stats["skipped_battery"] = level
            return True
        try:
            recs = await self.records(serial, day)
        except HomeAssistantError as err:
            _LOGGER.warning("%s: record list failed: %s", name, err)
            stats["errors"] += 1
            return False
        saved = await self.hass.async_add_executor_job(self.saved_keys, serial, day)
        cutoff = datetime.now() - timedelta(minutes=STILL_RECORDING_MIN)
        ok = True
        for begin, end in recs:
            ref = ClipRef.of(serial, begin, end)
            if ref.key in saved:
                stats["already"] += 1
            elif end <= cutoff:
                try:
                    await self.ensure_clip(ref)
                    stats["new"] += 1
                except HomeAssistantError as err:
                    _LOGGER.debug("%s %s: %s", name, ref.key, err)
                    stats["errors"] += 1
                    ok = False
        return ok
