"""Media source: Camera -> Day -> Clip."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.media_player import BrowseError, MediaClass
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, VIEW_URL

TEXT = {
    "en": {
        "title": "Camera recordings",
        "today": "Today",
        "yesterday": "Yesterday",
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "clips": "clips",
    },
    "it": {
        "title": "Registrazioni telecamere",
        "today": "Oggi",
        "yesterday": "Ieri",
        "days": ["lun", "mar", "mer", "gio", "ven", "sab", "dom"],
        "clips": "clip",
    },
}


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    return EzvizRecordingsSource(hass)


class EzvizRecordingsSource(MediaSource):
    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass
        self.name = self._t("title")

    def _t(self, key: str):
        lang = (self.hass.config.language or "en").split("-")[0]
        return TEXT.get(lang, TEXT["en"])[key]

    @property
    def rec(self):
        return self.hass.data[DOMAIN]

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        parts = (item.identifier or "").split("/")
        if len(parts) != 3:
            raise Unresolvable("Not a clip")
        serial, day, key = parts
        try:
            await self.rec.ensure_clip(serial, day, key)
        except HomeAssistantError as err:
            raise Unresolvable(str(err)) from err
        url = async_sign_path(
            self.hass, f"{VIEW_URL}/{serial}/{day}/{key}.mp4", timedelta(hours=6)
        )
        return PlayMedia(url, "video/mp4")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        parts = [p for p in (item.identifier or "").split("/") if p]
        if not parts:
            return self._root()
        if len(parts) == 1:
            return self._days(parts[0])
        if len(parts) == 2:
            return await self._clips(parts[0], parts[1])
        raise BrowseError("Unknown item")

    @staticmethod
    def _node(ident, title, *, can_expand=True, children=None,
              media_class=MediaClass.DIRECTORY, play=False, child_class=None):
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=ident,
            media_class=media_class,
            media_content_type="video/mp4" if play else "",
            title=title,
            can_play=play,
            can_expand=can_expand,
            children=children,
            children_media_class=child_class,
        )

    def _root(self):
        cams = self.rec.cameras()
        kids = [self._node(s, n) for s, n in sorted(cams.items(), key=lambda x: x[1])]
        return self._node(None, self._t("title"), children=kids, child_class=MediaClass.DIRECTORY)

    def _days(self, serial):
        name = self.rec.cameras().get(serial, serial)
        today = datetime.now().date()
        names = self._t("days")
        kids = []
        for i in range(self.rec.media_days):
            d = today - timedelta(days=i)
            if i == 0:
                label = self._t("today")
            elif i == 1:
                label = self._t("yesterday")
            else:
                label = f"{names[d.weekday()]} {d:%d/%m}"
            kids.append(self._node(f"{serial}/{d:%Y-%m-%d}", label))
        return self._node(serial, name, children=kids, child_class=MediaClass.DIRECTORY)

    async def _clips(self, serial, day):
        name = self.rec.cameras().get(serial, serial)
        try:
            recs = await self.rec.records(serial, day)
        except HomeAssistantError as err:
            raise BrowseError(f"{name}: {err}") from err
        kids = []
        for begin, end in sorted(recs, reverse=True):
            secs = int((end - begin).total_seconds())
            kids.append(
                self._node(
                    f"{serial}/{day}/{self.rec.key(begin, end)}",
                    f"{begin:%H:%M:%S} · {secs}s",
                    can_expand=False,
                    media_class=MediaClass.VIDEO,
                    play=True,
                )
            )
        d = datetime.strptime(day, "%Y-%m-%d")
        return self._node(
            f"{serial}/{day}",
            f"{name} — {d:%d/%m/%Y} ({len(kids)} {self._t('clips')})",
            children=kids,
            child_class=MediaClass.VIDEO,
        )
