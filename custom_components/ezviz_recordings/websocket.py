"""Websocket commands used by the cards."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .clip import ClipRef, check_day
from .const import DOMAIN, STILL_RECORDING_MIN
from .recordings import Recordings


def async_register_commands(hass: HomeAssistant) -> None:
    for command in (ws_cameras, ws_clips, ws_clip_url, ws_sync):
        websocket_api.async_register_command(hass, command)


def _rec(hass: HomeAssistant) -> Recordings:
    return hass.data[DOMAIN]


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/cameras"})
@callback
def ws_cameras(hass: HomeAssistant, connection: Any, msg: dict) -> None:
    cameras = _rec(hass).cameras()
    connection.send_result(
        msg["id"], {"cameras": [{"serial": s, "name": n} for s, n in cameras.items()]}
    )


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/sync"})
@callback
def ws_sync(hass: HomeAssistant, connection: Any, msg: dict) -> None:
    """Download today's new clips in the background (rate limited server side)."""
    rec = _rec(hass)
    started = rec.request_sync()
    connection.send_result(msg["id"], {"started": started, "running": rec.auto_running})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/clips",
        vol.Required("serial"): str,
        vol.Required("day"): str,
        # refresh: re-read the whole day; update: fetch what is new (incremental)
        vol.Optional("refresh", default=False): bool,
        vol.Optional("update", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_clips(hass: HomeAssistant, connection: Any, msg: dict) -> None:
    rec = _rec(hass)
    serial, day = msg["serial"], msg["day"]
    stale = False
    try:
        check_day(serial, day)
        hit = None if (msg["refresh"] or msg["update"]) else rec.cache.get(serial, day)
        if hit is not None:
            recs, fresh = hit
            stale = not fresh  # answer at once; the card asks for an update next
        else:
            recs = await rec.records(serial, day, full=msg["refresh"])
            # the camera is awake now: fetch the clips of this day it still has to give us
            rec.request_camera_sync(serial, day)
        clips_saved = await hass.async_add_executor_job(rec.saved_keys, serial, day)
        thumbs_saved = await hass.async_add_executor_job(rec.saved_keys, serial, day, "jpg")
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "ezviz_error", str(err))
        return
    clips = []
    missing = 0  # finished clips not saved yet (what a download run would fetch)
    cutoff = datetime.now() - timedelta(minutes=STILL_RECORDING_MIN)
    for begin, end in reversed(recs):  # newest first
        ref = ClipRef.of(serial, begin, end)
        saved = ref.key in clips_saved
        if not saved and end <= cutoff:
            missing += 1
        has_thumb = saved or ref.key in thumbs_saved  # a saved clip gets its thumb on request
        clips.append(
            {
                "key": ref.key,
                "begin": f"{begin:%H:%M:%S}",
                "seconds": int(ref.seconds),
                "cached": saved,
                "thumb": rec.signed_url(ref, "jpg", connection.refresh_token_id) if has_thumb else None,
            }
        )
    connection.send_result(
        msg["id"],
        {"serial": serial, "day": day, "clips": clips, "stale": stale, "running": rec.downloading(serial, day),
         "missing": missing},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/clip_url",
        vol.Required("serial"): str,
        vol.Required("day"): str,
        vol.Required("key"): str,
    }
)
@websocket_api.async_response
async def ws_clip_url(hass: HomeAssistant, connection: Any, msg: dict) -> None:
    """Download the clip if needed; signed URLs of the video and its thumbnail."""
    rec = _rec(hass)
    try:
        ref = ClipRef.parse(msg["serial"], msg["day"], msg["key"])
        await rec.ensure_clip(ref)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "ezviz_error", str(err))
        return
    token = connection.refresh_token_id
    connection.send_result(
        msg["id"],
        {"url": rec.signed_url(ref, "mp4", token), "thumb": rec.signed_url(ref, "jpg", token)},
    )
