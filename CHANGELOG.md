# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.52] — 2026-08-12

### Fixed
- **Assigning a room to a satellite in Home Assistant now reaches the manager.**
  It previously did not, until the next Home Assistant restart — the panel kept
  showing the old room, or none, which looks broken rather than stale.

  Two separate causes, both in 0.1.51:

  - The registry listener fired only on `create` and `remove`, with a comment
    asserting that only add/remove could change the roster. That was wrong: the
    roster carries `name` and `area`, and both change under `action="update"`.
  - A room is normally assigned to the **device**, not the entity, which emits a
    *device* registry event. Only the entity registry was being watched, so the
    path people actually use was never observed at all.

  Both registries are now watched, filtered to the fields the roster actually
  sends, and debounced by 2 seconds so a rename that emits several events costs
  one push. A device event only triggers a push if that device owns an
  `assist_satellite` entity.

  The predicates deciding this live in `roster.py` beside `collect()`, because
  they have to agree with what `collect()` sends — and a test asserts they cover
  every field it reads. They drifted apart once already, and the resulting
  failure was silent.

## [0.1.51] — 2026-08-12

### Added
- **The integration now tells the streamer which satellites Home Assistant
  knows about.** On startup and whenever an `assist_satellite` entity is added
  or removed, it pushes a roster — entity id, friendly name, area, and every
  candidate satellite id it can see.

  This replaces the one capability that breaks when the streamer runs outside
  Home Assistant. It used to read the entity registry itself via
  `http://supervisor/core/api/states`, which works only for an add-on, where the
  Supervisor injects a token. **A long-lived HA token was deliberately not used
  as the replacement** — those grant full control of every entity in the house
  in exchange for populating a list, and the trust today runs one way only: the
  integration authenticates to the streamer, which holds no HA credential at all.

  The roster is display data, not routing. A device sends its ESPHome node name
  (`atoms3r-echo-bca1a8`); HA knows an entity slug carrying the friendly name
  and a platform suffix (`assist_satellite.atoms3r_echo_base_voice_assistant_assist_satellite`).
  Those are different strings for the same device, so each entry carries a list
  of candidates rather than a single guess, and the streamer resolves against
  the ids it has actually seen devices send.

  Best-effort throughout: a streamer that is down or slow cannot disturb Home
  Assistant startup, and the streamer's existing "record whatever calls in
  without a mapping" behaviour remains the backstop that always works. The
  roster only makes a satellite appear *before* it has spoken.

  Requires the streamer to expose `POST /satellites` (ctrlable-announce, or a
  future add-on build); older streamers simply reject it and the push is logged
  at debug and forgotten.

## [0.1.50] — 2026-08-12

### Added
- **Each satellite can now be routed to its own streamer.** A config entry may
  name the satellite ids it serves; resolution runs most specific first — an
  entry that names the satellite, else an entry with an empty list (the
  catch-all), else the first entry.

  This exists to make migrating one room at a time possible. `_get_client()`
  previously returned `next(iter(entries))`, so a single streamer served every
  satellite and changing its URL moved all four rooms at once — if anything
  regressed, everything regressed together.

  **Existing installs are unaffected.** A lone entry has no satellites list, so
  it is the catch-all and answers for everything exactly as before. Nothing
  changes until a second entry is added.

  Matching is exact, deliberately: `atoms3r` is a prefix of
  `atoms3r-echo-bca1a8`, and with fuzzy matching an entry claiming the short
  name would silently capture the longer satellite — audio would simply go to
  the wrong streamer, with no error anywhere.

  Multiple config entries are now permitted (duplicate URLs are still refused,
  since two entries pointing at the same streamer is a mistake rather than a
  migration), and entries are titled by their satellites or URL so the wrong one
  is harder to edit by accident.

## [0.1.31] — 2026-08-09

### Fixed
- **Announcements were silent after a snapserver restart.** Snapserver
  regenerates group ids when it restarts, so the `announce_group_id` cached per
  client goes stale on every host reboot. `announce()` matched on that stored
  id, found nothing, and **silently skipped the stream switch** — then streamed
  the answer into the per-client announcement stream while the client was still
  subscribed to `Annoucements`. Bytes sent, add-on reported success, nothing
  audible.
  The group is now found by **membership** (which group currently contains this
  client), the stored id is refreshed when it has changed, and the
  no-group-found case logs a warning instead of failing quietly.

## [0.1.30] — 2026-08-09

### Fixed
- **Playback duration is now measured from the PCM we push, not from ffprobe.**
  ffprobe cannot measure a Home Assistant `tts_proxy` URL: HA serves those with
  **no `Content-Length`**, and ffprobe returns `duration=N/A` for a headerless
  MP3 stream. So every real answer probed as `0.0`, skipped the playback wait
  added in 0.1.26, and returned in ~1.6s — while tests against static files,
  which do have a length, passed. That is why it looked fixed.
  We already decode to s16le/48k/stereo, so the byte count *is* the duration,
  exactly, with no second process and no dependency on what the server
  advertises.
- Streaming behaviour is unchanged: bytes are counted inside the existing
  chunked loop, so first audio still reaches Snapcast within milliseconds. Only
  the HTTP response is delayed, so that "returned" means "the room stopped
  talking".

## [0.1.29] — 2026-08-08

### Added
- **`POST /prewarm`** — the endpoint every LVA satellite has been calling since
  it was written, and which never existed. Each satellite 404'd, retried six
  times with back-off, logged `prewarm failed after retries` and gave up: ~25s
  per satellite per boot, and the format cache stayed cold so the first announce
  after any restart paid `detect_format` on the critical path. Pass
  `satellite_id` to warm the cache for its mapped clients; without it the call
  still validates the URL and reports format and duration.

### Fixed
- `probe_duration` logs why it failed instead of returning a bare `0.0`. An
  unreachable URL was indistinguishable from a broken probe — a dead test server
  read as "ffprobe is failing" while ffprobe was saying "Connection refused"
  into `DEVNULL`.

## [0.1.28] — 2026-08-08

### Fixed
- Version reporting again: 0.1.27 routed it through `BUILD_VERSION`, which the
  published-add-on builder passes but Supervisor's **local** build does not, so
  `ADDON_VERSION` arrived empty and the web UI showed the `0.0.0-dev` fallback.
  `config.yaml` is now copied into the image and read at startup when the env
  var is absent. Order is env → shipped `config.yaml` → `0.0.0-dev`, so there is
  no build-arg dependency left.

## [0.1.27] — 2026-08-08

### Fixed
- **One version string, not three.** `config.yaml` said 0.1.26, `/status` said
  0.1.24 (a hardcoded `VERSION` in `main.py` marked "keep in sync" that wasn't),
  and the web UI badge said 0.1.0 (hardcoded in `base.html` since day one). The
  Dockerfile now takes `BUILD_VERSION` — which the add-on builder already passes
  from `config.yaml` — into `ADDON_VERSION`, `main.py` reads it, and it is
  injected into every template. `config.yaml` is the only place a version is
  written. This also means `/status` is finally trustworthy for confirming what
  is actually running.
- CoreS3 config: corrected `snapcast_satellite_id` and the announcing entity id.
  The satellite_id is the HA assist_satellite entity slug derived from the
  FRIENDLY name, not the ESPHome device name — verified against the live add-on
  as `m5stack-cores3-voice-assistant-e39e44-assist-satellite`. The previous value
  would have 404'd on every announce.

## [0.1.26] — 2026-08-08

### Fixed
- **`/announce` now returns when playback actually ends, not when the push does.**
  Measured: a 20-second clip pushed in 0.084s and the call returned in 1.6s
  while the room played for the full 20 seconds. Neither streaming path is
  rate-limited, so Snapcast buffers the whole clip and keeps playing long after
  our socket closes — but every caller treats the return as "the answer
  finished". That is how a voice satellite decides to stop showing "replying"
  and resume its wake word, so satellites were going idle and listening again
  while their own answer was still coming out of the speakers beside the mic.
  `announce()` now probes the clip length with ffprobe and sleeps out the
  remainder before returning. Capped at 300s so a mis-probed URL cannot pin a
  client's lock.
- Deliberately not fixed with `ffmpeg -re`: throttling the push to real time
  would make streaming fragile to network hiccups. Filling Snapcast's buffer
  fast is robust — push fast, return late.
- `AnnounceResult.duration` is now total wall time (≈ end of playback) rather
  than push time, and a new `audio_duration` reports the probed clip length.
  Both surface in the `/announce*` responses.
- Integration HTTP timeout 30s → 180s: announce calls now last as long as the
  reply, and 30s would fail on any long answer.

### Note
This makes the `binary_sensor.<satellite>_announcing` sensor added in 0.1.25
truthful. Before this change it went OFF ~1.6s into every answer.

## [0.1.25] — 2026-08-08

### Added
- **`binary_sensor.<satellite_id>_announcing`** — ON for exactly the span of an
  announce. Because the add-on awaits playback before returning, its ON→OFF edge
  is the moment the answer actually stopped playing in the room.
- ESPHome config for the M5Stack CoreS3 (`esphome/cores3-va-e39e44.yaml`), built
  as m5stack's official satellite base plus a marked Snapcast-routing diff, so
  the display, touchscreen and per-phase conversation UX are all retained.

### Fixed
- **Satellites that route audio to Snapcast no longer get stuck in the wrong
  state.** They had no way to learn when their answer finished: `on_end` fires
  when the *pipeline* ends, which is before playback begins, so the device went
  idle and resumed its wake word while the reply was still coming out of the
  speakers next to its microphone. `EVENT_ANNOUNCED` already marked the right
  moment, but ESPHome can only mirror entity state — it cannot subscribe to HA
  events — hence the new binary_sensor.
- `handle_announce` now clears the announcing state in a `finally`, so none of
  the five early-return error paths can leave a satellite latched in "replying".

## [0.1.0] — 2026-05-13

### Added (M3 — HA Custom Integration)
- `custom_components/ctrlable_snapcast_tts/` — HACS-installable HA integration
- Config flow: add-on URL + bearer token, tested via `/health`
- Options flow: menu-driven add/remove of satellite→snapclient mappings (wake-word aware)
- `ctrlable_snapcast_tts.announce` service — routes TTS URL to Snapcast via add-on API
- `ctrlable_snapcast_tts.set_mapping` service — programmatic mapping upsert
- `ctrlable_snapcast_tts_announced` bus event fired after each announcement

### Added (M2 — Streaming Pipeline)
- `POST /announce` and `POST /announce/multi` HTTP endpoints (bearer token auth)
- ffmpeg PCM pipeline for non-WAV TTS (Piper MP3 → s16le 48kHz stereo → Snapcast TCP)
- Format detection + per-source-host cache
- Home group auto-detection and restore after announcement
- Snapcast stream scan + adoption (Strategy 1: adopt existing TCP streams)
- Streams UI tab with snippet, scan button, and per-client status
- Auto-reconnect on dropped Snapcast JSON-RPC connection

### Added (M1 — Add-on Foundation)
- HA Supervisor add-on scaffold with ingress + bearer-token auth
- Snapcast JSON-RPC client (snapcast.py)
- `/health`, `/snapcast/clients`, `/snapcast/groups` endpoints
- Per-client announce port provisioning and persistence
- Clients and Groups UI tabs

[Unreleased]: https://github.com/Ctrlable/ha-ctrlable-snapcast-tts/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Ctrlable/ha-ctrlable-snapcast-tts/releases/tag/v0.1.0
