"""EZVIZ Recordings: browse, play and keep the SD-card / internal-storage clips of EZVIZ cameras.

Works on top of the `ezviz_rtsp` integration ("Ezviz Cloud with Video") and reuses its
logged-in EZVIZ client (no second login). Clips are fetched through the EZVIZ cloud
relay, so it also works with battery cameras that have no local RTSP.

Clips are saved as MP4 (+ a small JPEG thumbnail) in <storage_dir>/<serial>/<date>/
and kept until <storage_limit_gb>; then whole oldest days are deleted.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import voluptuous as vol
from aiohttp import web

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import (
    Event,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    AUTO_RETRY_DELAY,
    AUTO_SECOND_PASS_DELAY,
    CARD_FILE,
    CARD_URL,
    COMPLETE_MARGIN_MIN,
    CONF_AUTO_DOWNLOAD,
    CONF_AUTO_HOURS,
    CONF_MEDIA_DAYS,
    CONF_MIN_BATTERY,
    CONF_REQUIRE_MOUNT,
    CONF_STORAGE_DIR,
    CONF_STORAGE_LIMIT_GB,
    DEFAULT_AUTO_HOURS,
    DEFAULT_MEDIA_DAYS,
    DEFAULT_MIN_BATTERY,
    DEFAULT_STORAGE_DIR,
    DEFAULT_STORAGE_LIMIT_GB,
    DOMAIN,
    LIST_CACHE_SECONDS,
    RECORDS_KEEP_DAYS,
    SOURCE_DOMAIN,
    SYNC_MIN_INTERVAL,
    THUMB_URL,
    THUMB_WIDTH,
    VIEW_URL,
)
from .playback import TS_FMT, PlaybackError, check_library, download_ps, list_records

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Any(
            None,
            vol.Schema(
                {
                    vol.Optional(CONF_STORAGE_DIR, default=DEFAULT_STORAGE_DIR): cv.string,
                    vol.Optional(
                        CONF_STORAGE_LIMIT_GB, default=DEFAULT_STORAGE_LIMIT_GB
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.5)),
                    # a mount point (e.g. an NFS/SMB share): never save if it isn't mounted
                    vol.Optional(CONF_REQUIRE_MOUNT): cv.string,
                    vol.Optional(CONF_AUTO_DOWNLOAD, default=True): cv.boolean,
                    vol.Optional(CONF_AUTO_HOURS, default=DEFAULT_AUTO_HOURS): vol.All(
                        cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=0, max=23))]
                    ),
                    vol.Optional(CONF_MIN_BATTERY, default=DEFAULT_MIN_BATTERY): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=100)
                    ),
                    vol.Optional(CONF_MEDIA_DAYS, default=DEFAULT_MEDIA_DAYS): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=3650)
                    ),
                }
            ),
        )
    },
    extra=vol.ALLOW_EXTRA,
)

SERIAL_RE = re.compile(r"^[A-Z0-9]{6,20}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KEY_RE = re.compile(r"^\d{6}_\d{6}$")
DURATION_RE = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
MIN_COMPLETE_RATIO = 0.6  # a saved clip shorter than this share of its record is truncated


class Recordings:
    """Shared state: record listing cache, per-camera locks, downloads."""

    def __init__(self, hass: HomeAssistant, conf: dict) -> None:
        self.hass = hass
        self.cache_dir = Path(conf[CONF_STORAGE_DIR])
        self.limit_bytes = int(conf[CONF_STORAGE_LIMIT_GB] * 1024**3)
        self.require_mount = conf.get(CONF_REQUIRE_MOUNT)
        self.min_battery = conf[CONF_MIN_BATTERY]
        self.media_days = conf[CONF_MEDIA_DAYS]
        self._store: Store = Store(hass, 1, f"{DOMAIN}.records")
        self._db: dict[str, dict[str, dict]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._auto_running = False
        self._last_sync = -SYNC_MIN_INTERVAL
        self._retry_unsub = None
        self.last_auto: dict | None = None

    # -- cameras ------------------------------------------------------------
    def _coordinators(self) -> list:
        return [
            e.runtime_data
            for e in self.hass.config_entries.async_entries(SOURCE_DOMAIN)
            if getattr(e, "runtime_data", None) is not None
            and getattr(e.runtime_data, "ezviz_client", None) is not None
        ]

    def _camera_data(self, serial: str) -> dict | None:
        for coord in self._coordinators():
            data = (coord.data or {}).get(serial)
            if isinstance(data, dict):
                return data
        return None

    def cameras(self) -> dict[str, str]:
        """serial -> friendly name."""
        out: dict[str, str] = {}
        for coord in self._coordinators():
            for serial, data in (coord.data or {}).items():
                if isinstance(data, dict) and SERIAL_RE.match(str(serial)):
                    out[str(serial)] = data.get("name") or str(serial)
        return out

    def client(self, serial: str):
        coords = self._coordinators()
        for coord in coords:
            if serial in (coord.data or {}):
                return coord.ezviz_client
        if coords:
            return coords[0].ezviz_client
        raise HomeAssistantError(
            "The EZVIZ integration 'ezviz_rtsp' (Ezviz Cloud with Video) is not set up"
        )

    def _check_camera(self, serial: str) -> None:
        data = self._camera_data(serial) or {}
        if data.get("encrypted"):
            raise HomeAssistantError(
                "Video encryption is on for this camera: turn it off in the EZVIZ app "
                "(encrypted recordings are not supported yet)"
            )

    def lock(self, serial: str) -> asyncio.Lock:
        return self._locks.setdefault(serial, asyncio.Lock())

    # -- records (persistent cache) -----------------------------------------
    # Stored per camera/day: {"fetched": iso, "complete": bool, "recs": [[B, E], ...]}.
    # A day fetched after it ended (+ margin) is complete and never asked again;
    # today is updated incrementally (only from the last known clip onwards).
    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._db = data.get("records", {}) if isinstance(data, dict) else {}
        await self.hass.async_add_executor_job(self._prune)

    def _prune(self) -> None:
        limit = (datetime.now() - timedelta(days=RECORDS_KEEP_DAYS)).strftime("%Y-%m-%d")
        for serial, days in self._db.items():
            for day in [d for d in days if d < limit]:
                if not (self.cache_dir / serial / day).is_dir():  # saved files keep the list
                    days.pop(day)

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"records": self._db}, 10)

    def cached_records(self, serial: str, day: str) -> tuple[list, bool] | None:
        """(records, fresh) from cache, or None when the day was never fetched."""
        entry = self._db.get(serial, {}).get(day)
        if not entry:
            return None
        recs = [
            (datetime.strptime(b, TS_FMT), datetime.strptime(e, TS_FMT))
            for b, e in entry["recs"]
        ]
        fetched = datetime.fromisoformat(entry["fetched"])
        fresh = entry["complete"] or (datetime.now() - fetched).total_seconds() < LIST_CACHE_SECONDS
        return recs, fresh

    async def records(
        self, serial: str, day: str, *, full: bool = False
    ) -> list[tuple[datetime, datetime]]:
        """Records of a day: from cache when complete/fresh, else fetched (incrementally)."""
        if not (SERIAL_RE.match(serial) and DAY_RE.match(day)):
            raise HomeAssistantError("Invalid id")
        if not full:
            hit = self.cached_records(serial, day)
            if hit and hit[1]:
                return hit[0]
        async with self.lock(serial):
            if not full:
                hit = self.cached_records(serial, day)
                if hit and hit[1]:
                    return hit[0]
            day_start = datetime.strptime(day, "%Y-%m-%d")
            day_stop = day_start + timedelta(days=1) - timedelta(seconds=1)
            entry = None if full else self._db.get(serial, {}).get(day)
            old = [] if entry is None else [
                (datetime.strptime(b, TS_FMT), datetime.strptime(e, TS_FMT))
                for b, e in entry["recs"]
            ]
            start = day_start
            if old:
                start = max(day_start, max(b for b, _ in old) - timedelta(minutes=1))
            now = datetime.now()
            try:
                new = await self.hass.async_add_executor_job(
                    list_records, self.client(serial), serial, start, day_stop
                )
            except PlaybackError as err:
                raise HomeAssistantError(str(err)) from err
            merged = sorted({*old, *new})
            # drop records swallowed by a longer version of the same clip
            # (a clip still being written at the previous fetch grows its end)
            clean: list[tuple[datetime, datetime]] = []
            for b, e in merged:
                if clean and clean[-1][0] == b:
                    clean[-1] = (b, max(e, clean[-1][1]))
                else:
                    clean.append((b, e))
            self._db.setdefault(serial, {})[day] = {
                "fetched": now.isoformat(timespec="seconds"),
                "complete": now > day_stop + timedelta(minutes=COMPLETE_MARGIN_MIN),
                "recs": [[b.strftime(TS_FMT), e.strftime(TS_FMT)] for b, e in clean],
            }
            self._save()
            return clean

    def cached_keys(self, serial: str, day: str, ext: str = "mp4") -> set[str]:
        """Keys of saved clips (ext="mp4") or saved thumbnails (ext="jpg")."""
        folder = self.cache_dir / serial / day
        if not folder.is_dir():
            return set()
        return {
            f.stem for f in folder.glob(f"*.{ext}")
            if ".part." not in f.name and f.stat().st_size > 0
        }

    # -- clips ----------------------------------------------------------------
    @staticmethod
    def key(begin: datetime, end: datetime) -> str:
        return f"{begin:%H%M%S}_{end:%H%M%S}"

    def path(self, serial: str, day: str, key: str) -> Path:
        return self.cache_dir / serial / day / f"{key}.mp4"

    async def ensure_clip(self, serial: str, day: str, key: str) -> Path:
        if not (SERIAL_RE.match(serial) and DAY_RE.match(day) and KEY_RE.match(key)):
            raise HomeAssistantError("Invalid clip id")
        target = self.path(serial, day, key)
        if await self.hass.async_add_executor_job(_nonempty, target):
            return target
        self._check_camera(serial)
        b_s, e_s = key.split("_")
        begin = datetime.strptime(f"{day} {b_s}", "%Y-%m-%d %H%M%S")
        end = datetime.strptime(f"{day} {e_s}", "%Y-%m-%d %H%M%S")
        if end <= begin:
            end += timedelta(days=1)
        async with self.lock(serial):
            if await self.hass.async_add_executor_job(_nonempty, target):
                return target
            try:
                ps = await self.hass.async_add_executor_job(
                    download_ps, self.client(serial), serial, begin, end
                )
            except PlaybackError as err:
                raise HomeAssistantError(f"Download failed: {err}") from err
            await self._remux(ps, target)
            expected = (end - begin).total_seconds()
            got = await self._duration(target)
            if got is not None and got < max(1.0, expected * MIN_COMPLETE_RATIO):
                # truncated transfer: drop it so the next attempt downloads it again
                await self.hass.async_add_executor_job(target.unlink)
                raise HomeAssistantError(
                    f"Incomplete clip ({got:.1f} s of {expected:.0f} s), try again"
                )
        try:
            await self.ensure_thumb(serial, day, key)
        except HomeAssistantError as err:
            _LOGGER.debug("Thumbnail %s/%s/%s failed: %s", serial, day, key, err)
        return target

    async def ensure_thumb(self, serial: str, day: str, key: str) -> Path | None:
        """Small JPEG from the middle of a saved clip; None if the clip isn't saved."""
        if not (SERIAL_RE.match(serial) and DAY_RE.match(day) and KEY_RE.match(key)):
            raise HomeAssistantError("Invalid clip id")
        clip = self.path(serial, day, key)
        thumb = clip.with_suffix(".jpg")
        if await self.hass.async_add_executor_job(_nonempty, thumb):
            return thumb
        if not await self.hass.async_add_executor_job(_nonempty, clip):
            return None
        b_s, e_s = key.split("_")
        secs = (
            datetime.strptime(e_s, "%H%M%S") - datetime.strptime(b_s, "%H%M%S")
        ).total_seconds() % 86400
        real = await self._duration(clip)
        if real is not None:
            secs = min(secs, real)  # never seek past the real end of the file
        tmp = thumb.with_name(thumb.stem + ".part.jpg")
        proc = await asyncio.create_subprocess_exec(
            get_ffmpeg_manager(self.hass).binary, "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, secs / 2):.1f}", "-i", str(clip), "-frames:v", "1",
            "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", "6", "-f", "image2", "-y", str(tmp),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode != 0 or not await self.hass.async_add_executor_job(_nonempty, tmp):
            raise HomeAssistantError(f"ffmpeg thumb failed: {err.decode(errors='ignore')[-200:]}")
        await self.hass.async_add_executor_job(os.replace, tmp, thumb)
        return thumb

    async def _duration(self, path: Path) -> float | None:
        """Duration in seconds read from ffmpeg's header dump; None if unknown."""
        proc = await asyncio.create_subprocess_exec(
            get_ffmpeg_manager(self.hass).binary, "-hide_banner", "-i", str(path),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=30)
        match = DURATION_RE.search(err.decode(errors="ignore"))
        if not match:
            return None
        h, m, s = match.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)

    def _make_dir(self, folder: Path) -> None:
        """Create a clip folder; never fill the HA disk if the required share is missing."""
        if self.require_mount and not os.path.ismount(self.require_mount):
            raise HomeAssistantError(f"{self.require_mount} is not mounted: clip not saved")
        folder.mkdir(parents=True, exist_ok=True)

    async def _remux(self, ps: bytes, target: Path) -> None:
        await self.hass.async_add_executor_job(self._make_dir, target.parent)
        tmp = target.with_name(target.stem + ".part.mp4")
        ffmpeg = get_ffmpeg_manager(self.hass).binary
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "mpeg", "-i", "pipe:0",
            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-tag:v", "hvc1",
            "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", "-y", str(tmp),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(ps), timeout=120)
        if proc.returncode != 0 or not await self.hass.async_add_executor_job(_nonempty, tmp):
            raise HomeAssistantError(f"ffmpeg failed: {err.decode(errors='ignore')[-300:]}")
        await self.hass.async_add_executor_job(os.replace, tmp, target)

    # -- automatic download of today's clips ------------------------------------
    def battery(self, serial: str) -> int | None:
        try:
            return int((self._camera_data(serial) or {}).get("battery_level"))
        except (TypeError, ValueError):
            return None

    @property
    def auto_running(self) -> bool:
        return self._auto_running

    def request_sync(self) -> bool:
        """Start a background download of today's new clips, at most every SYNC_MIN_INTERVAL."""
        now = time.monotonic()
        if self._auto_running or now - self._last_sync < SYNC_MIN_INTERVAL:
            return False
        self._last_sync = now
        self.hass.async_create_background_task(self.download_today(), f"{DOMAIN}_sync")
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
            failed = [s for s in cams if not await self._download_camera(s, cams[s], day, summary)]
            if failed:
                # a camera in deep (night) sleep may need a second knock
                await asyncio.sleep(AUTO_SECOND_PASS_DELAY)
                failed = [s for s in failed if not await self._download_camera(s, cams[s], day, summary)]
            if failed:
                names = ", ".join(cams[s] for s in failed)
                if retry and self._retry_unsub is None:
                    _LOGGER.warning("Automatic download incomplete for %s, retrying in %s min",
                                    names, AUTO_RETRY_DELAY // 60)

                    async def _retry(_now) -> None:
                        self._retry_unsub = None
                        await self.download_today(retry=False)

                    self._retry_unsub = async_call_later(self.hass, AUTO_RETRY_DELAY, _retry)
                else:
                    _LOGGER.warning("Automatic download incomplete for %s", names)
            _LOGGER.info("Automatic download of %s finished: %s", day, summary)
            self.last_auto = {"at": datetime.now().isoformat(timespec="seconds"), "summary": summary}
            return summary
        finally:
            self._auto_running = False

    async def _download_camera(self, serial: str, name: str, day: str, summary: dict) -> bool:
        """Download the missing clips of one camera; False if anything failed."""
        stats = summary.setdefault(name, {"new": 0, "errors": 0, "already": 0})
        if (self._camera_data(serial) or {}).get("encrypted"):
            stats["skipped_encrypted"] = 1
            return True
        level = self.battery(serial)
        if level is not None and level < self.min_battery:
            _LOGGER.info("%s: battery %s%%, automatic download skipped", name, level)
            stats["skipped_battery"] = level
            return True
        try:
            recs = await self.records(serial, day, full=False)
        except HomeAssistantError as err:
            _LOGGER.warning("%s: record list failed: %s", name, err)
            stats["errors"] += 1
            return False
        cached = await self.hass.async_add_executor_job(self.cached_keys, serial, day)
        cutoff = datetime.now() - timedelta(minutes=2)  # may still be recording
        ok = True
        for begin, end in recs:
            key = self.key(begin, end)
            if key in cached:
                stats["already"] += 1
                continue
            if end > cutoff:
                continue
            try:
                await self.ensure_clip(serial, day, key)
                stats["new"] += 1
            except HomeAssistantError as err:
                _LOGGER.debug("%s %s: %s", name, key, err)
                stats["errors"] += 1
                ok = False
        return ok

    def cleanup(self) -> None:
        """Half-written files always; whole oldest days while above the storage limit."""
        root = self.cache_dir
        if not root.is_dir():
            return
        day_sizes: dict[str, int] = {}
        day_dirs: dict[str, list[Path]] = {}
        for file in root.rglob("*.*"):
            try:
                if not file.is_file():
                    continue
                if ".part." in file.name:
                    file.unlink()
                    continue
                day = file.parent.name
                day_sizes[day] = day_sizes.get(day, 0) + file.stat().st_size
                if file.parent not in day_dirs.setdefault(day, []):
                    day_dirs[day].append(file.parent)
            except OSError:
                pass
        total = sum(day_sizes.values())
        today = datetime.now().strftime("%Y-%m-%d")
        for day in sorted(day_sizes):
            if total <= self.limit_bytes or day >= today:
                break
            for folder in day_dirs[day]:
                for file in folder.glob("*"):
                    try:
                        file.unlink()
                    except OSError:
                        pass
            total -= day_sizes[day]
            _LOGGER.info("Storage limit reached: deleted recordings of %s", day)
        for folder in sorted(root.rglob("*"), reverse=True):
            if folder.is_dir():
                try:
                    folder.rmdir()
                except OSError:
                    pass


def _nonempty(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


class ClipView(HomeAssistantView):
    """Serve saved clips; auth required (the card and media source hand out signed URLs)."""

    url = VIEW_URL + "/{serial}/{day}/{key}"
    name = "api:ezviz_recordings:clip"
    requires_auth = True

    def __init__(self, rec: Recordings) -> None:
        self.rec = rec

    async def get(
        self, request: web.Request, serial: str, day: str, key: str
    ) -> web.StreamResponse:
        key = key.removesuffix(".mp4")
        try:
            path = await self.rec.ensure_clip(serial, day, key)
        except HomeAssistantError as err:
            return web.Response(status=502, text=str(err))
        return web.FileResponse(
            path,
            headers={"Content-Type": "video/mp4", "Cache-Control": "private, max-age=86400"},
        )


class ThumbView(HomeAssistantView):
    """Serve the small preview of a saved clip (never triggers a download)."""

    url = THUMB_URL + "/{serial}/{day}/{key}"
    name = "api:ezviz_recordings:thumb"
    requires_auth = True

    def __init__(self, rec: Recordings) -> None:
        self.rec = rec

    async def get(
        self, request: web.Request, serial: str, day: str, key: str
    ) -> web.StreamResponse:
        key = key.removesuffix(".jpg")
        try:
            path = await self.rec.ensure_thumb(serial, day, key)
        except HomeAssistantError as err:
            return web.Response(status=502, text=str(err))
        if path is None:
            return web.Response(status=404)
        return web.FileResponse(
            path,
            headers={"Content-Type": "image/jpeg", "Cache-Control": "private, max-age=86400"},
        )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config.get(DOMAIN) or CONFIG_SCHEMA({DOMAIN: {}})[DOMAIN]
    problem = check_library()
    if problem:
        _LOGGER.error("EZVIZ Recordings disabled: %s", problem)
        return False

    rec = Recordings(hass, conf)
    await rec.async_load()
    hass.data[DOMAIN] = rec
    hass.http.register_view(ClipView(rec))
    hass.http.register_view(ThumbView(rec))
    await hass.async_add_executor_job(rec.cleanup)

    async def _cleanup(_now) -> None:
        await hass.async_add_executor_job(rec.cleanup)
        await hass.async_add_executor_job(rec._prune)

    # just after midnight: enforce the storage limit
    async_track_time_change(hass, _cleanup, hour=0, minute=5, second=0)

    if conf[CONF_AUTO_DOWNLOAD]:

        async def _auto(_now) -> None:
            await rec.download_today()

        if conf[CONF_AUTO_HOURS]:
            async_track_time_change(hass, _auto, hour=conf[CONF_AUTO_HOURS], minute=0, second=0)
        # a last pass before midnight for the evening clips
        async_track_time_change(hass, _auto, hour=23, minute=50, second=0)

    async def _service_download(_call: ServiceCall) -> ServiceResponse:
        return {"result": await rec.download_today()}

    hass.services.async_register(
        DOMAIN, "download_today", _service_download,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _check_source(_event: Event) -> None:
        if not rec._coordinators():
            _LOGGER.error(
                "EZVIZ Recordings needs the 'ezviz_rtsp' integration (Ezviz Cloud with Video), "
                "set up with your EZVIZ account; no cameras found"
            )

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _check_source)

    card = Path(__file__).parent / "frontend" / CARD_FILE
    content = await hass.async_add_executor_job(card.read_bytes)
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(card), False)])
    add_extra_js_url(hass, f"{CARD_URL}?v={hashlib.sha256(content).hexdigest()[:12]}")

    from .websocket import async_register_commands

    async_register_commands(hass)
    return True
