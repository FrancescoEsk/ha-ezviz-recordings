"""Blocking helpers: list SD-card records and download one clip via the EZVIZ cloud relay.

Request format from pyEzvizApi PR #417 (cloud-mediated SD playback), verified on
eLife battery cameras: /playback path, begin/end as local wall-clock with a
literal Z, cln=3, StreamInfoReq field 8 = 10, a first 5302 redirect.
"""

from __future__ import annotations

import logging
import socket
import time
from datetime import datetime
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

_REQUIRED = (
    "VtmStreamClient",
    "build_stream_info_request",
    "parse_stream_info_response",
    "_proto_varint",
    "VTM_MAGIC",
    "VTM_HEADER_SIZE",
    "VtmMessageCode",
)


def check_library() -> str | None:
    """None if the installed pyezvizapi has what we need, else a readable problem."""
    try:
        from importlib.metadata import version

        installed = version("pyezvizapi")
    except Exception:  # noqa: BLE001
        installed = "?"
    if st is None or get_cloud_stream_info is None:
        return f"pyezvizapi {installed} has no cloud stream module (tested with 1.0.5.0)"
    missing = [name for name in _REQUIRED if not hasattr(st, name)]
    if missing or not hasattr(EzvizClient, "search_records_v2"):
        return f"pyezvizapi {installed} is not supported (missing {', '.join(missing) or 'search_records_v2'}); tested with 1.0.5.0"
    return None

CLIENT_VERSION = "v3.9.0.20251120"
CLIENT_TYPE = 3
TS_FMT = "%Y-%m-%dT%H:%M:%S"
RETRY_WAITS = (3, 5, 7, 10, 15)  # s between record-search attempts (~40 s in all)
IDLE_END_SECONDS = 5  # no packet for this long after video started = clip finished


class PlaybackError(Exception):
    """Clip could not be listed or fetched."""


def list_records(
    client: EzvizClient, serial: str, start: datetime, stop: datetime, wake: bool = True
) -> list[tuple[datetime, datetime]]:
    """Return (begin, end) local naive datetimes of SD records in [start, stop).

    wake=True (default) retries for ~40 s so a sleeping camera can wake up.
    """
    out: list[tuple[datetime, datetime]] = []
    cursor = start
    for _ in range(20):
        payload = None
        # A sleeping battery camera answers 2004 (DEVICE_EXCEPTION 70) while it
        # wakes up; at night that can take well over 10 s, so back off ~40 s.
        waits = RETRY_WAITS if wake else RETRY_WAITS[:2]
        for attempt in range(len(waits) + 1):
            try:
                payload = client.search_records_v2(
                    serial, 1, cursor.strftime("%Y-%m-%d %H:%M:%S"),
                    stop.strftime("%Y-%m-%d %H:%M:%S"), size=100,
                )
                break
            except PyEzvizError as err:
                if attempt == len(waits):
                    raise PlaybackError(f"Record search failed: {err}") from err
                _LOGGER.debug("%s asleep? retry in %ss (%s)", serial, waits[attempt], err)
                time.sleep(waits[attempt])
        raw = payload.get("records")
        recs = EzvizClient.decode_records_payload(raw) if raw else []
        batch = []
        for rec in recs:
            try:
                batch.append((datetime.strptime(rec["B"], TS_FMT), datetime.strptime(rec["E"], TS_FMT)))
            except (KeyError, ValueError, TypeError):
                continue
        out.extend(b for b in batch if b not in out)
        if str(payload.get("isFinished")) == "1" or not batch:
            break
        nxt = max(e for _, e in batch)
        if nxt <= cursor:
            break
        cursor = nxt
    out.sort()
    return out


def _wire_time(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def _playback_url(client: EzvizClient, serial: str, begin: datetime, end: datetime) -> str:
    info = get_cloud_stream_info(client, serial, channel=1, client_type=CLIENT_TYPE, refresh_vtm=True)
    parts = urlsplit(info["stream_url"])
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    stamp = int(query.get("timestamp") or time.time() * 1000)
    query.pop("vip", None)
    query.update(
        begin=_wire_time(begin), end=_wire_time(end), serial=serial,
        etp="1", rnd=str(stamp & 0x7FFFFFFF), cln=str(CLIENT_TYPE),
    )
    return urlunsplit((parts.scheme, parts.netloc, "/playback", urlencode(query), ""))


def _recv_exact(sock: socket.socket, length: int) -> bytes | None:
    chunks = []
    while length:
        chunk = sock.recv(length)
        if not chunk:
            return None
        chunks.append(chunk)
        length -= len(chunk)
    return b"".join(chunks)


def _read_packet(sock: socket.socket) -> tuple[int, int, bytes] | None:
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


def download_ps(client: EzvizClient, serial: str, begin: datetime, end: datetime) -> bytes:
    """Fetch one clip as MPEG-PS bytes."""
    duration = max(1.0, (end - begin).total_seconds())
    url = _playback_url(client, serial, begin, end)
    vtm = st.VtmStreamClient(url, timeout=20, client_version=CLIENT_VERSION)
    try:
        key = None
        info = None
        for _ in range(4):
            vtm.connect()
            req = st.build_stream_info_request(vtm.stream_url, vtm_stream_key=key, client_version=CLIENT_VERSION)
            vtm.send_packet(req + st._proto_varint(8, 10))
            info = None
            for _ in range(20):
                pkt = _read_packet(vtm._socket)
                if pkt is None:
                    raise PlaybackError("VTM closed before stream info")
                if pkt[1] == st.VtmMessageCode.STREAMINFO_RSP:
                    info = st.parse_stream_info_response(pkt[2])
                    break
            if info is None:
                raise PlaybackError("No stream info from VTM")
            if info.result and info.streamurl and info.vtmstreamkey:
                vtm.close()
                vtm.stream_url, key = info.streamurl, info.vtmstreamkey
                continue
            break
        if info is None or info.result not in (None, 0):
            raise PlaybackError(f"Playback refused by EZVIZ (result {info.result if info else '?'})")
        vtm.stream_info = info

        sock = vtm._socket
        sock.settimeout(8)
        buf = bytearray()
        counts: dict[int, int] = {}
        started = last_ka = time.monotonic()
        hard_limit = max(60.0, duration * 4 + 30)
        while time.monotonic() - started < hard_limit and len(buf) < 300_000_000:
            try:
                pkt = _read_packet(sock)
            except (TimeoutError, socket.timeout):
                if buf:
                    break
                raise PlaybackError("Camera sent no video (asleep or offline?)") from None
            if pkt is None:
                break
            channel, code, body = pkt
            counts[channel] = counts.get(channel, 0) + 1
            if code == st.VtmMessageCode.KEEPALIVE_REQ:
                vtm.send_keepalive(message_code=st.VtmMessageCode.KEEPALIVE_RSP)
                continue
            # Media arrives on 0x01 and, in playback mode, also on 0x02
            # (pyEzvizApi PLAYBACK_STREAM). A tiny 0x02 body is the vendor
            # trailer / end marker, not video.
            if channel == 0x02 and len(body) <= 64:
                continue
            if channel in (0x01, 0x02, 0x0B) and body:
                if not buf:
                    sock.settimeout(IDLE_END_SECONDS)  # once video flows, a pause = end
                buf += body
            if info.streamssn and time.monotonic() - last_ka > 5:
                vtm.send_keepalive()
                last_ka = time.monotonic()
        _LOGGER.debug("Clip %s %s: %d bytes, packets per channel %s", serial, begin, len(buf), counts)
        if not buf:
            raise PlaybackError("Empty clip")
        return bytes(buf)
    except OSError as err:
        raise PlaybackError(f"Network error: {err}") from err
    finally:
        vtm.close()
