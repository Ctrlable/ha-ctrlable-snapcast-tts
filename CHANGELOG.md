# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
