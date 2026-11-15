"""EZVIZ Recordings: browse, play and keep the SD-card / internal-storage clips of EZVIZ cameras.

Works on top of the `ezviz_rtsp` integration ("Ezviz Cloud with Video") and reuses its
logged-in EZVIZ client (no second login). Clips are fetched through the EZVIZ cloud
relay, so it also works with battery cameras that have no local RTSP.

Modules: playback (EZVIZ cloud protocol), records (cached record lists), media (ffmpeg),
clip (clip ids and file names), recordings (shared state, download, retention),
views / websocket / media_source (what the frontend talks to).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .const import (
    CARD_FILE,
    CARD_URL,
    CONF_AUTO_DOWNLOAD,
    CONF_AUTO_HOURS,
    CONF_LANGUAGE,
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
)
from .playback import check_library
from .recordings import Recordings
from .views import ClipView, ThumbView
from .websocket import async_register_commands

_LOGGER = logging.getLogger(__name__)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_STORAGE_DIR, default=DEFAULT_STORAGE_DIR): cv.string,
        vol.Optional(CONF_STORAGE_LIMIT_GB, default=DEFAULT_STORAGE_LIMIT_GB): vol.All(
            vol.Coerce(float), vol.Range(min=0.5)
        ),
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
        vol.Optional(CONF_LANGUAGE): cv.string,
    }
)
# `ezviz_recordings:` with no options is allowed
CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Any(None, OPTIONS_SCHEMA)}, extra=vol.ALLOW_EXTRA
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    problem = await hass.async_add_executor_job(check_library)  # reads package metadata
    if problem:
        _LOGGER.error("EZVIZ Recordings disabled: %s", problem)
        return False

    conf = config.get(DOMAIN) or OPTIONS_SCHEMA({})
    rec = Recordings(hass, conf)
    await rec.async_load()
    hass.data[DOMAIN] = rec
    hass.http.register_view(ClipView(rec))
    hass.http.register_view(ThumbView(rec))
    async_register_commands(hass)
    await _register_card(hass)
    await hass.async_add_executor_job(rec.cleanup)
    _schedule(hass, rec, conf)

    async def _download(_call: ServiceCall) -> ServiceResponse:
        return {"result": await rec.download_today()}

    hass.services.async_register(
        DOMAIN, "download_today", _download, supports_response=SupportsResponse.OPTIONAL
    )

    async def _check_source(_event: Event) -> None:
        if not rec.cameras():
            _LOGGER.error(
                "EZVIZ Recordings needs the 'ezviz_rtsp' integration (Ezviz Cloud with Video), "
                "set up with your EZVIZ account; no cameras found"
            )

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _check_source)
    return True


def _schedule(hass: HomeAssistant, rec: Recordings, conf: dict) -> None:
    async def _cleanup(_now) -> None:
        await hass.async_add_executor_job(rec.cleanup)
        await hass.async_add_executor_job(rec.prune)

    # just after midnight: enforce the storage limit
    async_track_time_change(hass, _cleanup, hour=0, minute=5, second=0)

    if not conf[CONF_AUTO_DOWNLOAD]:
        return

    async def _auto(_now) -> None:
        await rec.download_today()

    if conf[CONF_AUTO_HOURS]:
        async_track_time_change(hass, _auto, hour=conf[CONF_AUTO_HOURS], minute=0, second=0)
    # a last pass before midnight for the evening clips
    async_track_time_change(hass, _auto, hour=23, minute=50, second=0)


async def _register_card(hass: HomeAssistant) -> None:
    """Serve the cards' JS; the URL carries a hash so browsers reload it after an update."""
    card = Path(__file__).parent / "frontend" / CARD_FILE
    content = await hass.async_add_executor_job(card.read_bytes)
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(card), False)])
    add_extra_js_url(hass, f"{CARD_URL}?v={hashlib.sha256(content).hexdigest()[:12]}")
