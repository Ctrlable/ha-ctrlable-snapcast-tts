"""TTS streaming pipeline — PCM passthrough and ffmpeg fallback."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
from dataclasses import dataclass

import httpx

from snapcast import get_client as get_snap
from state import get_state, save_state

_LOGGER = logging.getLogger(__name__)
_BUFFER_DRAIN = 1.5  # seconds after stream end before restoring group
# Hard ceiling on how long announce() will block waiting out playback. A URL
# that probes as an hour long must not pin a client's lock for an hour.
_MAX_PLAYBACK_WAIT = 300.0
_SILENCE_PADDING_MS = 300  # ms of silence prepended to every announcement
_locks: dict[str, asyncio.Lock] = {}


def _silence_pcm(sample_rate: int, channels: int, sample_width: int) -> bytes:
    """Return silent PCM frames for _SILENCE_PADDING_MS at the given format."""
    n_frames = int(sample_rate * _SILENCE_PADDING_MS / 1000)
    return bytes(n_frames * channels * sample_width)


def _lock(client_id: str) -> asyncio.Lock:
    if client_id not in _locks:
        _locks[client_id] = asyncio.Lock()
    return _locks[client_id]


@dataclass
class AnnounceResult:
    client_id: str
    duration: float        # total wall time of the call ~= when playback ended
    fmt: str
    audio_duration: float = 0.0   # probed length of the clip, 0.0 if unknown


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


async def probe_duration(url: str) -> float:
    """Length of the audio in seconds, or 0.0 if it cannot be determined.

    Not cached: TTS URLs are unique per utterance, so a cache would only grow.
    ffprobe on a remote URL costs tens of milliseconds.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-tls_verify", "0",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
        text = out.decode().strip()
        if not text:
            # Returning a bare 0.0 made an UNREACHABLE URL indistinguishable from
            # a broken probe. That cost real debugging time on 2026-08-08: a dead
            # test server read as "ffprobe is failing" when ffprobe was fine and
            # saying "Connection refused" into a DEVNULL.
            _LOGGER.warning(
                "ffprobe gave no duration for %s (exit %s): %s",
                url, proc.returncode, err.decode(errors="replace").strip()[:300] or "<no stderr>",
            )
            return 0.0
        return max(0.0, float(text))
    except Exception as exc:  # noqa: BLE001 - unknown length degrades, never fatal
        _LOGGER.warning("ffprobe failed for %s: %s", url, exc)
        return 0.0


async def _stream_pcm(url: str, host: str, port: int) -> None:
    """Fetch URL, strip 44-byte WAV header if present, pipe raw PCM to Snapcast TCP source."""
    header_stripped = False
    buf = b""
    sock_writer: asyncio.StreamWriter | None = None
    async with httpx.AsyncClient(verify=False, timeout=30) as http, http.stream("GET", url) as resp:
        async for chunk in resp.aiter_bytes(4096):
                buf += chunk
                if not header_stripped and len(buf) >= 44:
                    if buf[:4] == b"RIFF":
                        # Parse WAV header so silence matches the stream format.
                        channels = struct.unpack_from("<H", buf, 22)[0]
                        sample_rate = struct.unpack_from("<I", buf, 24)[0]
                        bits_per_sample = struct.unpack_from("<H", buf, 34)[0]
                        silence = _silence_pcm(sample_rate, channels, bits_per_sample // 8)
                        buf = buf[44:]
                    else:
                        silence = _silence_pcm(48000, 2, 2)
                    header_stripped = True
                    _, sock_writer = await asyncio.open_connection(host, port)
                    sock_writer.write(silence)
                    await sock_writer.drain()
                if header_stripped and buf:
                    assert sock_writer is not None
                    sock_writer.write(buf)
                    await sock_writer.drain()
                    buf = b""
    if sock_writer is not None:
        sock_writer.close()
        with contextlib.suppress(Exception):
            await sock_writer.wait_closed()


async def _stream_ffmpeg(url: str, host: str, port: int) -> int:
    """Decode URL via ffmpeg → s16le 48 kHz stereo → Snapcast TCP source.

    Returns the PCM byte count, which is what tells us how long the audio is.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-tls_verify", "0",
        "-i", url,
        "-f", "s16le", "-ar", "48000", "-ac", "2",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _, sock_writer = await asyncio.open_connection(host, port)
    written = 0
    try:
        sock_writer.write(_silence_pcm(48000, 2, 2))
        await sock_writer.drain()
        assert proc.stdout is not None
        while chunk := await proc.stdout.read(4096):
            sock_writer.write(chunk)
            await sock_writer.drain()
            written += len(chunk)
    finally:
        sock_writer.close()
        with contextlib.suppress(Exception):
            await sock_writer.wait_closed()
        with contextlib.suppress(Exception):
            proc.kill()
        await proc.wait()
    return written


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
        # Each announcement client lives permanently in its own Snapcast group.
        # When a user switches to AirPlay / Music Assistant / etc., Snapcast
        # changes the GROUP's stream_id — the client never leaves the group.
        # So we snapshot the group's current stream, switch it to the
        # per-client announcement stream, play, then restore.
        live_stream_id: str = ""
        needs_restore = False

        if cs.announce_group_id and cs.announce_stream_id:
            try:
                groups = await snap.list_groups()
                grp = next((g for g in groups if g.id == cs.announce_group_id), None)
                if grp:
                    live_stream_id = grp.stream_id
                    if live_stream_id != cs.announce_stream_id:
                        await snap.set_group_stream(cs.announce_group_id, cs.announce_stream_id)
                        needs_restore = True
                        _LOGGER.info(
                            "Switched %r stream %r → %r for announcement",
                            client_id, live_stream_id, cs.announce_stream_id,
                        )
            except Exception as exc:
                _LOGGER.warning("Could not switch stream for %r: %s — playing anyway", client_id, exc)

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
                # No byte count on this path; fall back to probing the source.
                audio_duration = await probe_duration(tts_url)
            else:
                # Duration from the PCM WE PUSHED, not from ffprobe.
                #
                # ffprobe cannot measure a Home Assistant tts_proxy URL: HA
                # serves those with NO Content-Length, and ffprobe returns
                # duration=N/A for a headerless MP3 stream. Every real answer
                # therefore probed as 0.0 and skipped the playback wait, while
                # tests against static files (which do have a length) passed --
                # which is exactly why this survived the first round of testing.
                #
                # We already decode to s16le 48kHz stereo, so the byte count IS
                # the duration, exactly, with no second process and no
                # dependency on what the server chose to advertise.
                pcm = await _stream_ffmpeg(tts_url, host, cs.announce_port)
                audio_duration = pcm / float(48000 * 2 * 2)
            push = time.monotonic() - t0

            # WHAT THE WAIT BELOW DOES AND DOES NOT DELAY.
            #
            # This add-on streams: ffmpeg decodes in chunks and each 4096-byte
            # block goes straight to Snapcast, so the room starts hearing the
            # answer within milliseconds. That is the design and it is untouched
            # -- the byte counter above rides along inside the same loop and
            # buffers nothing.
            #
            # The wait delays only the HTTP RESPONSE to the caller, so that
            # "this call returned" means "the room stopped talking". Playback
            # latency is unchanged. Do not be tempted to "fix" the slow response
            # by removing it; that is the bug, not the feature.
            #
            # It does hold this client's lock for the duration, which serialises
            # back-to-back announcements to the same zone. That is correct for a
            # speaker -- two answers should not overlap.
            #
            # THE PUSH IS NOT THE PLAYBACK.
            #
            # Neither streaming path is rate-limited, so we hand Snapcast the
            # whole clip as fast as the network and decoder allow; it buffers
            # everything and keeps playing long after our socket closes.
            # Measured 2026-08-08: a 20s clip pushed in 0.084s and this call
            # returned in 1.6s while the room played for the full 20s.
            #
            # Every caller treats this call returning as "the answer finished" --
            # that is how a voice satellite knows to stop showing "replying" and
            # resume its wake word. Returning early made satellites go idle and
            # start listening while their own answer was still playing out of the
            # speakers next to the microphone. So wait out the remainder.
            #
            # Deliberately NOT solved with `ffmpeg -re`: throttling the push to
            # real time would make streaming vulnerable to any network hiccup,
            # whereas filling Snapcast's buffer quickly is robust. Push fast,
            # return late.
            if audio_duration > 0:
                remaining = min(audio_duration - push, _MAX_PLAYBACK_WAIT)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            else:
                _LOGGER.warning(
                    "Could not probe duration of %s - returning after the push, "
                    "so callers may think playback ended early", tts_url,
                )

            await asyncio.sleep(_BUFFER_DRAIN)
            duration = time.monotonic() - t0
            _LOGGER.info(
                "Announced to %r: audio %.2fs, pushed in %.2fs, held %.2fs via %s",
                client_id, audio_duration, push, duration, fmt,
            )
        finally:
            if needs_restore and live_stream_id:
                with contextlib.suppress(Exception):
                    await snap.set_group_stream(cs.announce_group_id, live_stream_id)
                    _LOGGER.info(
                        "Restored %r stream %r → %r",
                        client_id, cs.announce_stream_id, live_stream_id,
                    )

    return AnnounceResult(
        client_id=client_id, duration=duration, fmt=fmt, audio_duration=audio_duration
    )


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
