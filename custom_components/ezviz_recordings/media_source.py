"""Media panel: Camera recordings -> camera -> day -> clip."""

from __future__ import annotations

from datetime import date, datetime, timedelta

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

from .clip import ClipRef
from .const import DOMAIN
from .recordings import Recordings

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


def _folder(ident: str | None, title: str, children: list, child_class: MediaClass) -> BrowseMediaSource:
    return BrowseMediaSource(
        domain=DOMAIN, identifier=ident, media_class=MediaClass.DIRECTORY,
        media_content_type="", title=title, can_play=False, can_expand=True,
        children=children, children_media_class=child_class,
    )


def _video(ref: ClipRef, title: str) -> BrowseMediaSource:
    return BrowseMediaSource(
        domain=DOMAIN, identifier=f"{ref.serial}/{ref.day}/{ref.key}", media_class=MediaClass.VIDEO,
        media_content_type="video/mp4", title=title, can_play=True, can_expand=False,
    )


class EzvizRecordingsSource(MediaSource):
    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass
        self.name = self._t("title")

    @property
    def rec(self) -> Recordings:
        return self.hass.data[DOMAIN]

    def _t(self, key: str):
        lang = (self.rec.language or self.hass.config.language or "en").split("-")[0].lower()
        return TEXT.get(lang, TEXT["en"])[key]

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        try:
            ref = ClipRef.parse(*(item.identifier or "").split("/"))
            await self.rec.ensure_clip(ref)
        except (TypeError, HomeAssistantError) as err:  # TypeError: not serial/day/key
            raise Unresolvable(str(err)) from err
        return PlayMedia(self.rec.signed_url(ref, "mp4"), "video/mp4")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        parts = [p for p in (item.identifier or "").split("/") if p]
        if not parts:
            return self._cameras()
        if len(parts) == 1:
            return self._days(parts[0])
        if len(parts) == 2:
            return await self._clips(*parts)
        raise BrowseError("Unknown item")

    def _cameras(self) -> BrowseMediaSource:
        cams = sorted(self.rec.cameras().items(), key=lambda cam: cam[1])
        return _folder(None, self._t("title"), [_folder(s, n, [], MediaClass.DIRECTORY) for s, n in cams],
                       MediaClass.DIRECTORY)

    def _day_label(self, day: date, today: date) -> str:
        if day == today:
            return self._t("today")
        if day == today - timedelta(days=1):
            return self._t("yesterday")
        return f"{self._t('days')[day.weekday()]} {day:%d/%m}"

    def _days(self, serial: str) -> BrowseMediaSource:
        today = datetime.now().date()
        days = [today - timedelta(days=i) for i in range(self.rec.media_days)]
        children = [_folder(f"{serial}/{d:%Y-%m-%d}", self._day_label(d, today), [], MediaClass.VIDEO)
                    for d in days]
        return _folder(serial, self.rec.cameras().get(serial, serial), children, MediaClass.DIRECTORY)

    async def _clips(self, serial: str, day: str) -> BrowseMediaSource:
        name = self.rec.cameras().get(serial, serial)
        try:
            recs = await self.rec.records(serial, day)
        except HomeAssistantError as err:
            raise BrowseError(f"{name}: {err}") from err
        children = []
        for begin, end in reversed(recs):  # newest first
            ref = ClipRef.of(serial, begin, end)
            children.append(_video(ref, f"{begin:%H:%M:%S} · {int(ref.seconds)}s"))
        title = f"{name} — {datetime.strptime(day, '%Y-%m-%d'):%d/%m/%Y} ({len(children)} {self._t('clips')})"
        return _folder(f"{serial}/{day}", title, children, MediaClass.VIDEO)
