"""Websocket commands used by the ezviz-recordings-card."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, THUMB_URL, VIEW_URL


def async_register_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_cameras)
    websocket_api.async_register_command(hass, ws_clips)
    websocket_api.async_register_command(hass, ws_clip_url)
    websocket_api.async_register_command(hass, ws_sync)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/sync"})
@callback
def ws_sync(hass: HomeAssistant, connection: Any, msg: dict) -> None:
    """Download today's new clips in the background (rate limited server side)."""
    rec = hass.data[DOMAIN]
    started = rec.request_sync()
    connection.send_result(msg["id"], {"started": started, "running": rec.auto_running})


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/cameras"})
@websocket_api.async_response
async def ws_cameras(hass: HomeAssistant, connection: Any, msg: dict) -> None:
    rec = hass.data[DOMAIN]
    connection.send_result(
        msg["id"], {"cameras": [{"serial": s, "name": n} for s, n in rec.cameras().items()]}
    )


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
    rec = hass.data[DOMAIN]
    serial, day = msg["serial"], msg["day"]
    stale = False
    try:
        hit = None if (msg["refresh"] or msg["update"]) else rec.cached_records(serial, day)
        if hit is not None:
            recs, fresh = hit
            stale = not fresh  # answer at once; the card asks for an update next
        else:
            recs = await rec.records(serial, day, full=msg["refresh"])
        cached = await hass.async_add_executor_job(rec.cached_keys, serial, day)
        thumbs = await hass.async_add_executor_job(rec.cached_keys, serial, day, "jpg")
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "ezviz_error", str(err))
        return
    clips = []
    for begin, end in sorted(recs, reverse=True):
        key = rec.key(begin, end)
        saved = key in cached
        clips.append(
            {
                "key": key,
                "begin": begin.strftime("%H:%M:%S"),
                "seconds": int((end - begin).total_seconds()),
                "cached": saved,
                # clip + thumbnail are kept until the storage limit
                "thumb": (
                    _thumb_url(hass, connection, serial, day, key)
                    if saved or key in thumbs
                    else None
                ),
            }
        )
    connection.send_result(
        msg["id"],
        {"serial": serial, "day": day, "clips": clips, "stale": stale, "running": rec.auto_running},
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
    rec = hass.data[DOMAIN]
    serial, day, key = msg["serial"], msg["day"], msg["key"]
    try:
        await rec.ensure_clip(serial, day, key)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "ezviz_error", str(err))
        return
    url = async_sign_path(
        hass,
        f"{VIEW_URL}/{serial}/{day}/{key}.mp4",
        timedelta(hours=6),
        refresh_token_id=connection.refresh_token_id,
    )
    connection.send_result(
        msg["id"], {"url": url, "thumb": _thumb_url(hass, connection, serial, day, key)}
    )


def _thumb_url(hass: HomeAssistant, connection: Any, serial: str, day: str, key: str) -> str:
    return async_sign_path(
        hass,
        f"{THUMB_URL}/{serial}/{day}/{key}.jpg",
        timedelta(hours=6),
        refresh_token_id=connection.refresh_token_id,
    )
