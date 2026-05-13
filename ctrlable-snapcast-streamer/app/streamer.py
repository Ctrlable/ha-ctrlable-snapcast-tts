"""TTS streaming pipeline — PCM passthrough and ffmpeg fallback."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

import httpx

from snapcast import get_client as get_snap
from state import get_state, save_state

_LOGGER = logging.getLogger(__name__)
_BUFFER_DRAIN = 1.5  # seconds after stream end before restoring group
_locks: dict[str, asyncio.Lock] = {}


def _lock(client_id: str) -> asyncio.Lock:
    if client_id not in _locks:
        _locks[client_id] = asyncio.Lock()
    return _locks[client_id]


@dataclass
class AnnounceResult:
    client_id: str
    duration: float
    fmt: str


class ClientNotFoundError(KeyError):
    pass


class ClientNotEnabledError(ValueError):
    pass


class NotProvisionedError(ValueError):
    pass


async def detect_format(url: str) -> str:
    """HEAD the URL; return 'pcm_wav' for WAV/PCM content, 'other' otherwise."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as http:
            resp = await http.head(url)
            ct = resp.headers.get("content-type", "")
    except Exception:
        return "other"
    return "pcm_wav" if any(x in ct for x in ("wav", "pcm", "x-wav")) else "other"


async def _stream_pcm(url: str, host: str, port: int) -> None:
    """Fetch URL, strip 44-byte WAV header if present, pipe raw PCM to Snapcast TCP source."""
    sock_writer: asyncio.StreamWriter | None = None
    header_stripped = False
    buf = b""
    async with httpx.AsyncClient(verify=False, timeout=30) as http, http.stream("GET", url) as resp:
        async for chunk in resp.aiter_bytes(4096):
                buf += chunk
                if not header_stripped and len(buf) >= 44:
                    buf = buf[44:] if buf[:4] == b"RIFF" else buf
                    header_stripped = True
                if header_stripped and buf:
                    if sock_writer is None:
                        _, sock_writer = await asyncio.open_connection(host, port)
                    sock_writer.write(buf)
                    await sock_writer.drain()
                    buf = b""
    if sock_writer is not None:
        sock_writer.close()
        with contextlib.suppress(Exception):
            await sock_writer.wait_closed()


async def _stream_ffmpeg(url: str, host: str, port: int) -> None:
    """Decode URL via ffmpeg → s16le 48 kHz stereo → Snapcast TCP source."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-tls_verify", "0",
        "-i", url,
        "-f", "s16le", "-ar", "48000", "-ac", "2",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _, sock_writer = await asyncio.open_connection(host, port)
    try:
        assert proc.stdout is not None
        while chunk := await proc.stdout.read(4096):
            sock_writer.write(chunk)
            await sock_writer.drain()
    finally:
        sock_writer.close()
        with contextlib.suppress(Exception):
            await sock_writer.wait_closed()
        with contextlib.suppress(Exception):
            proc.kill()
        await proc.wait()


async def announce(client_id: str, tts_url: str, source_host: str) -> AnnounceResult:
    state = get_state()
    cs = state.clients.get(client_id)
    if cs is None:
        raise ClientNotFoundError(client_id)
    if not cs.enabled:
        raise ClientNotEnabledError(client_id)
    if not cs.announce_port:
        raise NotProvisionedError(f"{client_id}: no announce port allocated")

    snap = get_snap()

    async with _lock(client_id):
        # Snapshot the client's live group NOW so we restore to wherever it
        # actually is — not to the group recorded at last-scan time.  This
        # handles manual source changes (e.g. switched to AirPlay) between
        # scans.
        live_group_id: str = cs.home_group_id  # safe fallback
        try:
            snap_clients = await snap.list_clients()
            match = next((c for c in snap_clients if c.id == client_id), None)
            if match:
                live_group_id = match.current_group_id
        except Exception as exc:
            _LOGGER.warning(
                "Could not fetch live group for %r (%s) — will restore to saved home_group",
                client_id, exc,
            )

        # Persist home group on first run if still empty
        if not cs.home_group_id and live_group_id:
            cs.home_group_id = live_group_id
            cs.home_group_autodetected = True
            save_state()
            _LOGGER.info("Auto-detected home group %r for %r", live_group_id, client_id)

        # Move to announce group only when client is not already there
        needs_restore = False
        if cs.announce_group_id and live_group_id and live_group_id != cs.announce_group_id:
            await snap.move_client_to_group(client_id, cs.announce_group_id)
            needs_restore = True

        try:
            fmt = cs.format_cache.get(source_host)
            if fmt is None:
                fmt = await detect_format(tts_url)
                cs.format_cache[source_host] = fmt
                save_state()
                _LOGGER.info("Format detected for %r from %r: %r", client_id, source_host, fmt)

            t0 = time.monotonic()
            host = state.snapcast.host
            if fmt == "pcm_wav":
                await _stream_pcm(tts_url, host, cs.announce_port)
            else:
                await _stream_ffmpeg(tts_url, host, cs.announce_port)
            duration = time.monotonic() - t0

            _LOGGER.info("Announced to %r: %.2fs via %s", client_id, duration, fmt)
            await asyncio.sleep(_BUFFER_DRAIN)
        finally:
            if needs_restore and live_group_id:
                with contextlib.suppress(Exception):
                    await snap.move_client_to_group(client_id, live_group_id)

    return AnnounceResult(client_id=client_id, duration=duration, fmt=fmt)


async def announce_multi(client_ids: list[str], tts_url: str, source_host: str) -> list[AnnounceResult]:
    """Announce to multiple clients in parallel; each client streams independently."""
    results = await asyncio.gather(
        *(announce(cid, tts_url, source_host) for cid in client_ids),
        return_exceptions=True,
    )
    out = []
    for cid, r in zip(client_ids, results, strict=False):
        if isinstance(r, AnnounceResult):
            out.append(r)
        else:
            _LOGGER.error("announce_multi: %r failed — %s", cid, r)
    return out
