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
# Seconds to keep the group on the announcement stream after the last byte is
# pushed. This is NOT slack -- it is what covers snapserver's output buffer.
# Restore the group sooner than the buffer and the audio is still queued when
# the subscriber changes, so it is discarded and NOTHING PLAYS while every layer
# reports success. Verified the hard way on 2026-08-09 with a 0.4s value against
# a 1000ms buffer.
#
# THIS VALUE IS COUPLED TO SNAPSERVER'S CONFIG:
#     /etc/snapserver-announcement.conf (LXC 113)   buffer = 400
# 1.2s. 0.9 was cut too fine and CLIPPED THE END OF ANSWERS: the drain has to
# outlast the whole output chain, not just the server buffer --
#
#     snapserver buffer      400ms   (/etc/snapserver-announcement.conf)
#     snapclient --latency    80ms
#     PulseAudio DAC        ~190ms   (measured: outputBufferDacTime 185-190)
#     ------------------------------
#     real tail             ~670ms
#
# 0.9s left only ~230ms of margin, so ordinary jitter was enough to restore the
# group's stream while the last syllable was still in flight. 1.2s restores the
# ~500ms slack the original 1.5s/1000ms pairing had.
# If that buffer is raised, RAISE THIS TOO.
#
# AND THERE IS A FLOOR ON THE BUFFER ITSELF, measured rather than guessed. 200ms
# was tried on 2026-08-09 and produced total silence -- every layer reporting
# success, nothing audible. The client said exactly why:
#
#     (Stream) outputBufferDacTime > bufferMs: 189 > 120
#
# PulseAudio's DAC latency on these clients is ~185-190ms, and snapclient
# subtracts its own `--latency 80` from the server buffer, so buffer=200 left
# 120ms to place chunks into and every one missed its deadline.
#
#     floor = PulseAudio DAC latency (~190ms) + client --latency (80ms) = ~270ms
#
# 400 sits above that with headroom for jitter. Do not go below ~350 without
# re-measuring outputBufferDacTime on the actual clients -- and note the failure
# is silent, so "it did not throw" is not evidence it worked.
_BUFFER_DRAIN = 1.2
# Hard ceiling on how long announce() will block waiting out playback. A URL
# that probes as an hour long must not pin a client's lock for an hour.
_MAX_PLAYBACK_WAIT = 300.0
# Silence prepended to every announcement so the first syllable is not clipped
# while the group settles on the new stream. It is also pure added latency --
# every ms here delays the sound reaching the room -- so chimes use a shorter
# one. An answer can afford 300ms of lead-in; a wake chime is feedback and its
# whole value is being prompt.
_SILENCE_PADDING_MS = 300
_CHIME_SILENCE_PADDING_MS = 60
_locks: dict[str, asyncio.Lock] = {}


def _silence_pcm(sample_rate: int, channels: int, sample_width: int,
                 padding_ms: int | None = None) -> bytes:
    """Return silent PCM frames for the given padding at the given format."""
    n_frames = int(sample_rate * (_SILENCE_PADDING_MS if padding_ms is None else padding_ms) / 1000)
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


def _tls_args(url: str) -> list[str]:
    """`-tls_verify 0` only where it is legal.

    It is a protocol option, so ffmpeg/ffprobe accept it only when the input is
    actually opened over TLS. Hand it a filesystem path -- which is how bundled
    chimes arrive -- and it dies before reading a byte:
        Option tls_verify not found.
        Error opening input file sounds/wake_word_triggered.flac
    The failure is silent from the caller's side: zero PCM bytes, zero duration,
    announcement "succeeds", nothing plays. Caught on the bench 2026-08-09
    before this shipped.
    """
    return ["-tls_verify", "0"] if url.startswith("https://") else []


async def detect_format(url: str) -> str:
    """HEAD the URL; return 'pcm_wav' for WAV/PCM content, 'other' otherwise."""
    # Bundled chimes come through here as filesystem paths. There is nothing to
    # HEAD, and ffmpeg reads them directly, so say so rather than routing a local
    # path into httpx just to catch the exception it raises.
    if not url.startswith(("http://", "https://")):
        return "other"
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
            "ffprobe", "-v", "error", *_tls_args(url),
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


async def _stream_ffmpeg(url: str, host: str, port: int, silence_ms: int | None = None) -> int:
    """Decode URL via ffmpeg → s16le 48 kHz stereo → Snapcast TCP source.

    Returns the PCM byte count, which is what tells us how long the audio is.
    """
    # stderr was DEVNULL, and that is how a decoder that never opened its input
    # looked exactly like a successful announcement: no bytes, no duration, no
    # complaint, every layer reporting OK. -loglevel error keeps the pipe to a
    # few bytes in the normal case so reading it after the fact cannot deadlock.
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error",
        *_tls_args(url),
        "-i", url,
        "-f", "s16le", "-ar", "48000", "-ac", "2",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, sock_writer = await asyncio.open_connection(host, port)
    written = 0
    try:
        sock_writer.write(_silence_pcm(48000, 2, 2, silence_ms))
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
        err = b""
        with contextlib.suppress(Exception):
            if proc.stderr is not None:
                err = await proc.stderr.read()
        with contextlib.suppress(Exception):
            proc.kill()
        await proc.wait()

    if written == 0:
        # Loud, because the whole class of bug this add-on keeps producing is
        # "reported success, nobody heard anything".
        _LOGGER.error(
            "ffmpeg decoded 0 bytes from %s (exit %s) — nothing was played: %s",
            url, proc.returncode, err.decode(errors="replace").strip()[:300] or "<no stderr>",
        )
    return written


# Last volume we pushed per client, so a satellite that sends its slider value on
# every single announce does not cost an RPC every single announce. In-memory
# only: after a restart the first announce re-applies, which is harmless.
_volume_cache: dict[str, int] = {}

# Groups deliberately left switched to their announcement stream between the
# announcements of one exchange, and the timer that guarantees they cannot stay
# that way. client_id -> (live_stream_id_to_restore, asyncio.TimerHandle-ish task)
#
# WHY HOLD IT. Restoring the group after every announcement is what made music
# pump: snapclient goes idle, closes its Pulse stream, module-role-ducking
# unducks, then the next chime re-ducks. Measured 0.22s of full-volume music
# between back-to-back announcements, and the whole chime/thinking/answer
# sequence produced three duck cycles.
#
# It also delayed the un-duck: because snapclient holds its Pulse stream ~5s
# past the audio, the host-side ducker could not tell "finished" from "idle" and
# needed a linger on top. Holding the group means the restore itself IS the
# end-of-exchange signal, and it lands ~1.2s after the last audio.
#
# THE RISK, and why the watchdog is not optional: an exchange that never
# produces an answer (wake word into silence, HA error, device reboot mid-turn)
# would leave the group pointed at a stream nobody is feeding -- that zone would
# play no music at all until something else announced. The watchdog restores it
# unconditionally.
_sticky: dict[str, str] = {}
_sticky_tasks: dict[str, asyncio.Task] = {}
STICKY_TIMEOUT = 20.0


async def _sticky_watchdog(client_id: str):
    """Restore a held group if the exchange never finishes."""
    try:
        await asyncio.sleep(STICKY_TIMEOUT)
    except asyncio.CancelledError:
        return
    # Never yank the group out from under an announcement that is mid-flight.
    # The lock is held for the whole of announce(), so if it is taken the
    # exchange is alive and this timeout is simply premature -- re-arm and let
    # the answer release it normally. Without this a slow reply (longer than
    # STICKY_TIMEOUT after the last chime) gets cut off by its own watchdog,
    # which the sticky-group test caught on the first run.
    if _lock(client_id).locked():
        _LOGGER.debug("Sticky watchdog for %r deferred: announce in flight", client_id)
        _sticky_tasks[client_id] = asyncio.create_task(_sticky_watchdog(client_id))
        return

    live = _sticky.pop(client_id, "")
    _sticky_tasks.pop(client_id, None)
    if not live:
        return
    state = get_state()
    cs = state.clients.get(client_id)
    if not cs:
        return
    _LOGGER.warning(
        "Sticky group for %r never released after %ss -- restoring %r. An "
        "exchange probably ended without an answer.", client_id, STICKY_TIMEOUT, live)
    with contextlib.suppress(Exception):
        await get_snap().set_group_stream(cs.announce_group_id, live)


def _arm_sticky(client_id: str, live_stream_id: str):
    _sticky[client_id] = live_stream_id
    old = _sticky_tasks.get(client_id)
    if old and not old.done():
        old.cancel()
    _sticky_tasks[client_id] = asyncio.create_task(_sticky_watchdog(client_id))


def _disarm_sticky(client_id: str) -> str:
    t = _sticky_tasks.pop(client_id, None)
    if t and not t.done():
        t.cancel()
    return _sticky.pop(client_id, "")


async def hold_group(client_id: str) -> bool:
    """Hold the zone without playing anything.

    Ducking follows announce audio, so a follow-up turn in a continued
    conversation had nothing holding it: the wake chime covers the FIRST turn,
    but when the assistant answers with a question and reopens the mic there is
    no chime, and music played at full volume straight into the microphone.

    Switching the group is enough on its own -- the host-side ducker triggers on
    `group.stream_id != IDLE_STREAM`, not on audio -- so this ducks the room
    without emitting a sound. Idempotent: if the group is already held this only
    re-arms the watchdog.
    """
    state = get_state()
    cs = state.clients.get(client_id)
    if cs is None or not cs.enabled or not cs.announce_stream_id:
        return False
    snap = get_snap()
    async with _lock(client_id):
        if client_id in _sticky:
            _arm_sticky(client_id, _sticky[client_id])   # refresh the watchdog
            return True
        try:
            groups = await snap.list_groups()
            grp = next((g for g in groups if client_id in g.client_ids), None)
            if grp is None:
                return False
            if grp.id != cs.announce_group_id:
                cs.announce_group_id = grp.id
                save_state()
            if grp.stream_id == cs.announce_stream_id:
                return True                              # already switched
            await snap.set_group_stream(cs.announce_group_id, cs.announce_stream_id)
            _arm_sticky(client_id, grp.stream_id)
            _LOGGER.info("Holding %r on %r for listening (no audio)",
                         client_id, cs.announce_stream_id)
        except Exception as exc:                                 # noqa: BLE001
            _LOGGER.warning("Could not hold group for %r: %s", client_id, exc)
            return False
    return True


async def release_group(client_id: str) -> bool:
    """Restore a held group now, because the exchange ended without an answer.

    The sticky group is released by the ANSWER. An exchange that never produces
    one -- a false wake, STT recognising nothing, an error mid-turn -- has
    nothing to release it, so the zone would stay ducked until STICKY_TIMEOUT.
    False wakes are common enough that ~20s of quiet music each time is a real
    annoyance, and shortening the timeout instead would risk releasing early
    during a slow intent (measured 2-9s).

    Takes the client lock rather than racing it: if an announcement is in flight
    this waits, and by the time it runs that announcement has already disarmed
    the hold, so this correctly becomes a no-op.
    """
    if client_id not in _sticky:
        return False
    async with _lock(client_id):
        live = _disarm_sticky(client_id)
        if not live:
            return False
        cs = get_state().clients.get(client_id)
        if cs is None:
            return False
        try:
            await get_snap().set_group_stream(cs.announce_group_id, live)
        except Exception as exc:                                 # noqa: BLE001
            _LOGGER.warning("Could not release group for %r: %s", client_id, exc)
            return False
        _LOGGER.info("Released %r -> %r (exchange ended with no answer)",
                     client_id, live)
    return True


async def announce(
    client_id: str, tts_url: str, source_host: str, volume: int | None = None,
    drain: float | None = None, silence_ms: int | None = None,
    hold_group: bool = False,
) -> AnnounceResult:
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
        # Volume before the stream switch, so the level is already right when the
        # first chunk lands rather than ramping a word or two in.
        if volume is not None and _volume_cache.get(client_id) != int(volume):
            try:
                await snap.set_client_volume(client_id, int(volume))
                _volume_cache[client_id] = int(volume)
                _LOGGER.info("Set %r volume to %d%%", client_id, int(volume))
            except Exception as exc:
                # Never fail an announcement over a volume change -- a reply at
                # the wrong level still beats no reply.
                _LOGGER.warning("Could not set volume for %r: %s", client_id, exc)

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
                # Find the group by MEMBERSHIP, not by the stored id.
                #
                # Snapserver regenerates group ids when it restarts, so a cached
                # id goes stale on every reboot of the host. The old code matched
                # on the stored id, found nothing, and SILENTLY skipped the stream
                # switch -- then streamed the answer into the per-client
                # announcement stream while the client was still listening to
                # "Annoucements". Bytes sent, add-on reports success, nothing
                # audible. Diagnosed 2026-08-09 after a host reboot:
                #   stored  announce_group_id 9096e53b (gone)
                #   actual  snapclient-announcement-6#16 in group daeb57f1
                grp = next((g for g in groups if client_id in g.client_ids), None)
                if grp is None:
                    grp = next((g for g in groups if g.id == cs.announce_group_id), None)
                if grp is not None and grp.id != cs.announce_group_id:
                    _LOGGER.info(
                        "Group id for %r changed %r -> %r (snapserver restart?); updating",
                        client_id, cs.announce_group_id, grp.id,
                    )
                    cs.announce_group_id = grp.id
                    save_state()
                if grp is None:
                    # Never silent again: this is the state that produced a
                    # "successful" announce nobody could hear.
                    _LOGGER.warning(
                        "No snapcast group contains %r and stored group %r is gone -- "
                        "streaming anyway, but nothing is subscribed so it will be inaudible",
                        client_id, cs.announce_group_id,
                    )
                if grp:
                    live_stream_id = grp.stream_id
                    # A previous announcement in this exchange may have left the
                    # group switched. In that case the group's CURRENT stream is
                    # our announcement stream, not the music one -- remember the
                    # real stream to go back to, or we would "restore" the group
                    # to the announcement stream and strand it there.
                    held = _sticky.get(client_id, "")
                    if held:
                        live_stream_id = held
                    if grp.stream_id != cs.announce_stream_id:
                        await snap.set_group_stream(cs.announce_group_id, cs.announce_stream_id)
                        needs_restore = True
                    elif held:
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
                pcm = await _stream_ffmpeg(tts_url, host, cs.announce_port, silence_ms)
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

            # Chimes pass a shorter drain. The default is sized so a long answer
            # is fully out of the buffer before the group's live stream comes
            # back; a chime is under a second and holds this client's lock the
            # whole time, so the full 1.5s would push a fast answer back by that
            # much for no benefit.
            await asyncio.sleep(_BUFFER_DRAIN if drain is None else drain)
            duration = time.monotonic() - t0
            _LOGGER.info(
                "Announced to %r: audio %.2fs, pushed in %.2fs, held %.2fs via %s",
                client_id, audio_duration, push, duration, fmt,
            )
        finally:
            if needs_restore and live_stream_id:
                if hold_group:
                    # Leave it switched: another announcement in this exchange is
                    # expected, and restoring now is what makes music pump.
                    _arm_sticky(client_id, live_stream_id)
                    _LOGGER.info(
                        "Holding %r on %r (will restore to %r); watchdog %ss",
                        client_id, cs.announce_stream_id, live_stream_id, STICKY_TIMEOUT,
                    )
                else:
                    _disarm_sticky(client_id)
                    with contextlib.suppress(Exception):
                        await snap.set_group_stream(cs.announce_group_id, live_stream_id)
                        _LOGGER.info(
                            "Restored %r stream %r → %r",
                            client_id, cs.announce_stream_id, live_stream_id,
                        )

    return AnnounceResult(
        client_id=client_id, duration=duration, fmt=fmt, audio_duration=audio_duration
    )


async def announce_multi(
    client_ids: list[str], tts_url: str, source_host: str, volume: int | None = None,
    drain: float | None = None, silence_ms: int | None = None,
    hold_group: bool = False,
) -> list[AnnounceResult]:
    """Announce to multiple clients in parallel; each client streams independently."""
    results = await asyncio.gather(
        *(announce(cid, tts_url, source_host, volume, drain, silence_ms, hold_group)
          for cid in client_ids),
        return_exceptions=True,
    )
    out = []
    for cid, r in zip(client_ids, results, strict=False):
        if isinstance(r, AnnounceResult):
            out.append(r)
        else:
            _LOGGER.error("announce_multi: %r failed — %s", cid, r)
    return out
