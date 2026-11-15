"""Blocking EZVIZ cloud calls (run them in the executor): list SD records, download one clip.

Playback request from pyEzvizApi PR #417 (cloud-mediated SD playback), verified on
eLife battery cameras: /playback path, begin/end as local wall-clock with a literal
Z, cln=3, StreamInfoReq field 8 = 10, a first 5302 answer that redirects.
"""

from __future__ import annotations

import logging
import socket
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pyezvizapi import EzvizClient
from pyezvizapi.exceptions import PyEzvizError

# Internal pieces of pyezvizapi (installed by ezviz_rtsp). Tested with 1.0.5.0;
# version 2.0 moves them, so a missing piece disables the integration with a clear message.
try:
    from pyezvizapi import stream as st
    from pyezvizapi.cloud_stream import get_cloud_stream_info
except ImportError:  # pragma: no cover - depends on the installed library
    st = None
    get_cloud_stream_info = None

_LOGGER = logging.getLogger(__name__)

TESTED_VERSION = "1.0.5.0"
_REQUIRED = (
    "VtmStreamClient",
    "build_stream_info_request",
    "parse_stream_info_response",
    "_proto_varint",
    "VTM_MAGIC",
    "VTM_HEADER_SIZE",
    "VtmMessageCode",
)

TS_FMT = "%Y-%m-%dT%H:%M:%S"  # record times from the camera (local, naive)
CLIENT_VERSION = "v3.9.0.20251120"
CLIENT_TYPE = 3
RETRY_WAITS = (3, 5, 7, 10, 15)  # s between record-search attempts (~40 s in all)
STREAM_INFO_TIMEOUT = 10  # s, StreamInfoReq field 8
FIRST_DATA_TIMEOUT = 8  # s to wait for the first video packet
IDLE_END_SECONDS = 5  # no packet for this long after video started = clip finished
MAX_CLIP_BYTES = 300_000_000

CH_STREAM = 0x01
CH_PLAYBACK = 0x02  # media in playback mode; a tiny body here is the end marker
END_MARKER_MAX = 64


class PlaybackError(Exception):
    """A clip could not be listed or fetched."""


def check_library() -> str | None:
    """None if the installed pyezvizapi has what we need, else a readable problem."""
    try:
        installed = version("pyezvizapi")
    except PackageNotFoundError:
        installed = "?"
    if st is None or get_cloud_stream_info is None:
        return f"pyezvizapi {installed} has no cloud stream module (tested with {TESTED_VERSION})"
    missing = [name for name in _REQUIRED if not hasattr(st, name)]
    if not hasattr(EzvizClient, "search_records_v2"):
        missing.append("search_records_v2")
    if missing:
        return (f"pyezvizapi {installed} is not supported (missing {', '.join(missing)}); "
                f"tested with {TESTED_VERSION}")
    return None


# -- record list ----------------------------------------------------------------
def list_records(
    client: EzvizClient, serial: str, start: datetime, stop: datetime
) -> list[tuple[datetime, datetime]]:
    """(begin, end) of the SD records in [start, stop], oldest first, following pages."""
    out: set[tuple[datetime, datetime]] = set()
    cursor = start
    for _ in range(20):  # pages of 100
        payload = _search(client, serial, cursor, stop)
        batch = _parse_records(payload.get("records"))
        out.update(batch)
        if str(payload.get("isFinished")) == "1" or not batch:
            break
        last_end = max(end for _, end in batch)
        if last_end <= cursor:
            break
        cursor = last_end
    return sorted(out)


def _search(client: EzvizClient, serial: str, start: datetime, stop: datetime) -> dict[str, Any]:
    # A sleeping battery camera answers 2004 (DEVICE_EXCEPTION 70) while it wakes
    # up; at night that can take well over 10 s, hence the back-off.
    for wait in (*RETRY_WAITS, None):
        try:
            return client.search_records_v2(
                serial, 1, start.strftime("%Y-%m-%d %H:%M:%S"),
                stop.strftime("%Y-%m-%d %H:%M:%S"), size=100,
            )
        except PyEzvizError as err:
            if wait is None:
                raise PlaybackError(f"Record search failed: {err}") from err
            _LOGGER.debug("%s asleep? retry in %ss (%s)", serial, wait, err)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _parse_records(raw: str | None) -> list[tuple[datetime, datetime]]:
    out = []
    for rec in EzvizClient.decode_records_payload(raw) if raw else []:
        try:
            out.append((datetime.strptime(rec["B"], TS_FMT), datetime.strptime(rec["E"], TS_FMT)))
        except (KeyError, ValueError, TypeError):
            continue
    return out


# -- clip download ----------------------------------------------------------------
def download_ps(client: EzvizClient, serial: str, begin: datetime, end: datetime) -> bytes:
    """Fetch one clip as MPEG-PS bytes."""
    vtm = st.VtmStreamClient(
        _playback_url(client, serial, begin, end), timeout=20, client_version=CLIENT_VERSION
    )
    try:
        vtm.stream_info = _negotiate(vtm)
        data = _receive(vtm, max(1.0, (end - begin).total_seconds()))
        _LOGGER.debug("Clip %s %s: %d bytes", serial, begin, len(data))
        return data
    except OSError as err:
        raise PlaybackError(f"Network error: {err}") from err
    finally:
        vtm.close()


def _playback_url(client: EzvizClient, serial: str, begin: datetime, end: datetime) -> str:
    """The live ysproto URL turned into a /playback one for [begin, end]."""
    info = get_cloud_stream_info(client, serial, channel=1, client_type=CLIENT_TYPE, refresh_vtm=True)
    parts = urlsplit(info["stream_url"])
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    stamp = int(query.get("timestamp") or time.time() * 1000)
    query.pop("vip", None)
    query.update(
        begin=f"{begin:%Y%m%dT%H%M%S}Z", end=f"{end:%Y%m%dT%H%M%S}Z", serial=serial,
        etp="1", rnd=str(stamp & 0x7FFFFFFF), cln=str(CLIENT_TYPE),
    )
    return urlunsplit((parts.scheme, parts.netloc, "/playback", urlencode(query), ""))


def _negotiate(vtm: Any) -> Any:
    """StreamInfoReq, following the redirect(s) to the server that has the stream."""
    key = None
    for _ in range(4):
        vtm.connect()
        request = st.build_stream_info_request(
            vtm.stream_url, vtm_stream_key=key, client_version=CLIENT_VERSION
        )
        vtm.send_packet(request + st._proto_varint(8, STREAM_INFO_TIMEOUT))
        info = _stream_info(vtm._socket)
        if info.result and info.streamurl and info.vtmstreamkey:  # 5302: go there
            vtm.close()
            vtm.stream_url, key = info.streamurl, info.vtmstreamkey
            continue
        if info.result not in (None, 0):
            raise PlaybackError(f"Playback refused by EZVIZ (result {info.result})")
        return info
    raise PlaybackError("Too many redirects from EZVIZ")


def _stream_info(sock: socket.socket) -> Any:
    for _ in range(20):
        packet = _read_packet(sock)
        if packet is None:
            raise PlaybackError("VTM closed before stream info")
        _, code, body = packet
        if code == st.VtmMessageCode.STREAMINFO_RSP:
            return st.parse_stream_info_response(body)
    raise PlaybackError("No stream info from VTM")


def _receive(vtm: Any, seconds: float) -> bytes:
    """Collect the video until the camera stops sending (or a safety limit)."""
    sock = vtm._socket
    sock.settimeout(FIRST_DATA_TIMEOUT)
    buf = bytearray()
    started = last_keepalive = time.monotonic()
    hard_limit = max(60.0, seconds * 4 + 30)
    while time.monotonic() - started < hard_limit and len(buf) < MAX_CLIP_BYTES:
        try:
            packet = _read_packet(sock)
        except TimeoutError:
            if buf:
                break  # a pause after the video = end of clip
            raise PlaybackError("Camera sent no video (asleep or offline?)") from None
        if packet is None:
            break
        channel, code, body = packet
        if code == st.VtmMessageCode.KEEPALIVE_REQ:
            vtm.send_keepalive(message_code=st.VtmMessageCode.KEEPALIVE_RSP)
            continue
        if channel in (CH_STREAM, CH_PLAYBACK) and len(body) > (
            END_MARKER_MAX if channel == CH_PLAYBACK else 0
        ):
            if not buf:
                sock.settimeout(IDLE_END_SECONDS)
            buf += body
        if vtm.stream_info.streamssn and time.monotonic() - last_keepalive > 5:
            vtm.send_keepalive()
            last_keepalive = time.monotonic()
    if not buf:
        raise PlaybackError("Empty clip")
    return bytes(buf)


def _read_packet(sock: socket.socket) -> tuple[int, int, bytes] | None:
    """(channel, message code, body) of one VTM packet; None when the socket closed."""
    header = _recv_exact(sock, st.VTM_HEADER_SIZE)
    if header is None:
        return None
    if header[0] != st.VTM_MAGIC:
        raise PlaybackError("VTM magic byte not found")
    length = int.from_bytes(header[2:4], "big")
    body = _recv_exact(sock, length) if length else b""
    if body is None:
        return None
    return header[1], int.from_bytes(header[6:8], "big"), body


def _recv_exact(sock: socket.socket, length: int) -> bytes | None:
    chunks = []
    while length:
        chunk = sock.recv(length)
        if not chunk:
            return None
        chunks.append(chunk)
        length -= len(chunk)
    return b"".join(chunks)
