# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
