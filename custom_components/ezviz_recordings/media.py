"""ffmpeg helpers: MPEG-PS -> MP4 remux, duration, thumbnail. Files are written atomically."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import THUMB_QUALITY, THUMB_WIDTH

DURATION_RE = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")


def part_file(target: Path) -> Path:
    """Temporary name while writing; cleanup removes leftovers ('.part.' in the name)."""
    return target.with_name(f"{target.stem}.part{target.suffix}")


def nonempty(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def jpeg_width(path: Path) -> int | None:
    """Width of a JPEG from its SOF header, None if unreadable (blocking)."""
    try:
        with path.open("rb") as file:
            if file.read(2) != b"\xff\xd8":
                return None
            while True:
                marker = file.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                size = int.from_bytes(file.read(2), "big")
                if marker[1] in (0xC0, 0xC1, 0xC2):  # baseline / extended / progressive frame
                    header = file.read(5)
                    return int.from_bytes(header[3:5], "big")
                file.seek(size - 2, os.SEEK_CUR)
    except OSError:
        return None


async def _ffmpeg(
    hass: HomeAssistant, *args: str, data: bytes | None = None, timeout: float = 60
) -> tuple[int, str]:
    """Run ffmpeg; return (exit code, stderr text)."""
    proc = await asyncio.create_subprocess_exec(
        get_ffmpeg_manager(hass).binary, "-hide_banner", *args,
        stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(data), timeout=timeout)
    except TimeoutError:
        proc.kill()
        raise HomeAssistantError("ffmpeg timed out") from None
    return proc.returncode, err.decode(errors="ignore")


async def _write(hass: HomeAssistant, target: Path, *args: str, data: bytes | None = None) -> None:
    """Run ffmpeg writing to a temporary file, then move it in place."""
    tmp = part_file(target)
    code, err = await _ffmpeg(hass, "-loglevel", "error", *args, "-y", str(tmp), data=data, timeout=120)
    if code != 0 or not await hass.async_add_executor_job(nonempty, tmp):
        raise HomeAssistantError(f"ffmpeg failed: {err[-300:]}")
    await hass.async_add_executor_job(os.replace, tmp, target)


async def remux_to_mp4(hass: HomeAssistant, ps: bytes, target: Path) -> None:
    """MPEG-PS from the camera -> MP4 without re-encoding (hvc1 tag for Apple, AAC audio)."""
    await _write(
        hass, target,
        "-f", "mpeg", "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "copy", "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart",
        data=ps,
    )


async def duration(hass: HomeAssistant, path: Path) -> float | None:
    """Duration in seconds from ffmpeg's header dump; None if unknown."""
    _, err = await _ffmpeg(hass, "-i", str(path), timeout=30)
    match = DURATION_RE.search(err)
    if not match:
        return None
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


async def thumbnail(hass: HomeAssistant, clip: Path, at: float, target: Path) -> None:
    """Small JPEG of the frame at `at` seconds."""
    await _write(
        hass, target,
        "-ss", f"{max(0.0, at):.1f}", "-i", str(clip), "-frames:v", "1",
        "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", str(THUMB_QUALITY), "-f", "image2",
    )
