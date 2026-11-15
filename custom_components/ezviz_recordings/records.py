"""Persistent cache of the record lists, in .storage/ezviz_recordings.records.

Per camera and day: {"fetched": iso, "complete": bool, "recs": [[B, E], ...]}.
A day fetched after it ended (+ margin) is complete and never asked again; the
current day is fresh for LIST_CACHE_SECONDS and then updated incrementally.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import COMPLETE_MARGIN_MIN, DOMAIN, LIST_CACHE_SECONDS, RECORDS_KEEP_DAYS
from .playback import TS_FMT

Span = tuple[datetime, datetime]


def day_bounds(day: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(day, "%Y-%m-%d")
    return start, start + timedelta(days=1) - timedelta(seconds=1)


def merge(old: list[Span], new: list[Span]) -> list[Span]:
    """Union of two fetches; a clip still being written grows its end, keep the longest."""
    out: list[Span] = []
    for begin, end in sorted({*old, *new}):
        if out and out[-1][0] == begin:
            out[-1] = (begin, max(end, out[-1][1]))
        else:
            out.append((begin, end))
    return out


class RecordCache:
    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store = Store(hass, 1, f"{DOMAIN}.records")
        self._db: dict[str, dict[str, dict]] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._db = data.get("records", {}) if isinstance(data, dict) else {}

    def prune(self, has_files: Callable[[str, str], bool]) -> None:
        """Forget old lists, unless files of that day are still saved (blocking: checks disk)."""
        limit = (datetime.now() - timedelta(days=RECORDS_KEEP_DAYS)).strftime("%Y-%m-%d")
        for serial, days in self._db.items():
            for day in [d for d in days if d < limit and not has_files(serial, d)]:
                days.pop(day)

    def get(self, serial: str, day: str) -> tuple[list[Span], bool] | None:
        """(records, fresh), or None when the day was never fetched."""
        entry = self._db.get(serial, {}).get(day)
        if not entry:
            return None
        age = (datetime.now() - datetime.fromisoformat(entry["fetched"])).total_seconds()
        return _decode(entry["recs"]), entry["complete"] or age < LIST_CACHE_SECONDS

    def put(self, serial: str, day: str, recs: list[Span], fetched: datetime) -> None:
        _, day_end = day_bounds(day)
        self._db.setdefault(serial, {})[day] = {
            "fetched": fetched.isoformat(timespec="seconds"),
            "complete": fetched > day_end + timedelta(minutes=COMPLETE_MARGIN_MIN),
            "recs": [[b.strftime(TS_FMT), e.strftime(TS_FMT)] for b, e in recs],
        }
        self._store.async_delay_save(lambda: {"records": self._db}, 10)


def _decode(recs: list[list[str]]) -> list[Span]:
    return [(datetime.strptime(b, TS_FMT), datetime.strptime(e, TS_FMT)) for b, e in recs]
