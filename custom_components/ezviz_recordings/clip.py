"""Identity of a clip: camera serial + day + time range, and where its files live."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from homeassistant.exceptions import HomeAssistantError

SERIAL_RE = re.compile(r"^[A-Z0-9]{6,20}$")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KEY_RE = re.compile(r"^\d{6}_\d{6}$")


def check_day(serial: str, day: str) -> None:
    """Raise if serial/day could not come from us (they end up in file paths)."""
    if not (SERIAL_RE.match(serial) and DAY_RE.match(day)):
        raise HomeAssistantError("Invalid camera or day")


@dataclass(frozen=True)
class ClipRef:
    """One recorded clip; `key` is "HHMMSS_HHMMSS" (begin_end, local time)."""

    serial: str
    day: str
    key: str

    @classmethod
    def parse(cls, serial: str, day: str, key: str) -> ClipRef:
        """Validated reference from user input (websocket, URL, media id)."""
        check_day(serial, day)
        if not KEY_RE.match(key):
            raise HomeAssistantError("Invalid clip id")
        return cls(serial, day, key)

    @classmethod
    def of(cls, serial: str, begin: datetime, end: datetime) -> ClipRef:
        return cls(serial, f"{begin:%Y-%m-%d}", f"{begin:%H%M%S}_{end:%H%M%S}")

    @property
    def span(self) -> tuple[datetime, datetime]:
        """(begin, end); a clip running past midnight ends the next day."""
        b_s, e_s = self.key.split("_")
        begin = datetime.strptime(f"{self.day} {b_s}", "%Y-%m-%d %H%M%S")
        end = datetime.strptime(f"{self.day} {e_s}", "%Y-%m-%d %H%M%S")
        if end <= begin:
            end += timedelta(days=1)
        return begin, end

    @property
    def seconds(self) -> float:
        begin, end = self.span
        return (end - begin).total_seconds()

    def file(self, root: Path, ext: str) -> Path:
        """<root>/<serial>/<day>/<key>.<ext>"""
        return root / self.serial / self.day / f"{self.key}.{ext}"
