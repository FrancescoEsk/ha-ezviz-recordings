"""HTTP endpoints serving the saved files (login required; clients get signed URLs)."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.exceptions import HomeAssistantError

from .clip import ClipRef
from .const import CLIP_URL, THUMB_URL
from .recordings import Recordings


class _FileView(HomeAssistantView):
    requires_auth = True
    ext: str
    content_type: str

    def __init__(self, rec: Recordings) -> None:
        self.rec = rec

    async def _path(self, ref: ClipRef) -> Path | None:
        raise NotImplementedError

    async def get(self, request: web.Request, serial: str, day: str, key: str) -> web.StreamResponse:
        try:
            path = await self._path(ClipRef.parse(serial, day, key.removesuffix(f".{self.ext}")))
        except HomeAssistantError as err:
            return web.Response(status=502, text=str(err))
        if path is None:
            return web.Response(status=404)
        return web.FileResponse(
            path,
            headers={"Content-Type": self.content_type, "Cache-Control": "private, max-age=86400"},
        )


class ClipView(_FileView):
    """The clip's MP4 (downloaded on first request)."""

    url = CLIP_URL + "/{serial}/{day}/{key}"
    name = "api:ezviz_recordings:clip"
    ext = "mp4"
    content_type = "video/mp4"

    async def _path(self, ref: ClipRef) -> Path | None:
        return await self.rec.ensure_clip(ref)


class ThumbView(_FileView):
    """The clip's thumbnail (never triggers a download)."""

    url = THUMB_URL + "/{serial}/{day}/{key}"
    name = "api:ezviz_recordings:thumb"
    ext = "jpg"
    content_type = "image/jpeg"

    async def _path(self, ref: ClipRef) -> Path | None:
        return await self.rec.ensure_thumb(ref)
