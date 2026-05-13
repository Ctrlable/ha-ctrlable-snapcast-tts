# M0 Verification Report

**Date:** 2026-05-13  
**Status:** COMPLETE — all four §7 items verified. No blockers. Architecture as specified is sound with amendments noted below.  
**Environment:** HAOS 2026.5.1 @ 10.1.8.23, Snapcast LXC @ 10.1.8.9, M5Stack Atom Echo a14640 (active), M5Stack Atom Echo a14654 (unavailable/offline)

---

## §7 M0 Verification Items

### 1. TTS URL Format from Piper — CONFIRMED

**Method:** Captured live production logs from `tts-stream-server.service` on the Snapcast LXC; inspected TTS cache files on HAOS; WAV PCM header analysis.

**Findings:**

**URL format in HA 2026.5.x (`on_intent_progress` / `on_tts_end`):**
```
https://11850-pinecrest-home-1.ctrlable.com:8123/api/tts_proxy/<short_token>.mp3
```

Sample live URLs captured from systemd logs:
- `https://11850-pinecrest-home-1.ctrlable.com:8123/api/tts_proxy/pzf_t62MQSb0byjBrWmTcA.mp3`
- `https://11850-pinecrest-home-1.ctrlable.com:8123/api/tts_proxy/Lm7CRxtCOvjdLTWRrAFaAg.mp3`

**Key properties:**
- Domain: Nabu Casa external URL (`11850-pinecrest-home-1.ctrlable.com:8123`), not `localhost:8123`
- Auth: **self-authenticating** — one-time token embedded in the URL path. No `Authorization:` header required.
- Format: **MP3** in all captured pipeline interactions (satellite YAML declares `format: MP3` in `media_player.announcement_pipeline`)
- TLS: Must use `verify=False` / `ffmpeg -tls_verify 0` — the SSL cert covers the external domain; local IP access fails cert validation
- The URL is accessed from the Snapcast LXC (10.1.8.9) over LAN; the token provides the auth

**TTS cache on disk:** `/config/tts/` — contains both `.mp3` and `.wav` files. WAV files are PCM (AudioFormat=1, 16kHz, 16-bit, 2ch). These are from direct API calls, not pipeline runs.

**Recommendation for M1:**
- **PCM direct passthrough is NOT viable** for pipeline TTS: URLs are MP3, not WAV. Always use the ffmpeg path.
- The `format_cache` / format detection in the spec is still correct architecture — a future voice pipeline variant or direct API call might return WAV. Keep the detection logic.
- ffmpeg must use `-tls_verify 0` when fetching TTS URLs from the add-on.
- The add-on's `httpx` client must use `verify=False` for the TTS URL fetch.

---

### 2. Snapcast Version + RPC Capabilities — CONFIRMED

**Method:** Connected to both Snapcast server instances via JSON-RPC TCP; tested `Stream.AddStream` and `Stream.RemoveStream`; inspected both `snapserver.conf` files.

**Version:** `snapserver v0.35.0 (rev f1237347)` ✓

**`Stream.AddStream` test:**
```json
→ {"id":2,"jsonrpc":"2.0","method":"Stream.AddStream","params":{"streamUri":"tcp://0.0.0.0:19999?name=m0test&mode=server&sampleformat=48000:16:2&codec=pcm"}}
← {"id":2,"jsonrpc":"2.0","result":{"id":"m0test"}}
```
`Stream.AddStream` **confirmed working** in v0.35.0. Also confirmed in snapserver journal:
```
(ControlRequest) Stream.AddStream(tcp://0.0.0.0:19999?name=m0test…)
```

`Stream.RemoveStream` also confirmed. Test stream cleaned up.

**CRITICAL ARCHITECTURE FINDING — Dual Snapcast Instance Setup:**

The LXC runs **two separate snapserver instances**, each with independent config, data dir, and RPC ports:

| Instance | Binary | Stream Port | TCP RPC | HTTP RPC | Config |
|----------|--------|-------------|---------|----------|--------|
| Main (music) | `snapserver` | 1704 | **1705** | 1780 | `/etc/snapserver.conf` |
| Announcement | `snapserver-announcement` | 1714 | **1715** | 1790 | `/etc/snapserver-announcement.conf` |

**Current connected clients — main server (8 clients, all music, all on 10.1.8.21):**

| Client ID | Name | Connected |
|-----------|------|-----------|
| `snapclient0#0` | MA Sunroom SC0 C3 | ✓ |
| `snapclient1` | MA Kitchen SC1 C6 | ✓ |
| `snapclient2#2` | MA Family Room SC2 C2 | ✓ |
| `snapclient3#3` | MA Master Bed SC3 C5 | ✓ |
| `snapclient4#4` | MA Master Bath SC4 C7 | ✓ |
| `snapclient5#5` | MA Terrace SC5 C0 | ✓ |
| `snapclient6#6` | MA Living Room SC6 C1 | ✓ (muted) |
| `snapclient7#7` | MA Dining Room SC7 C4 | ✓ |

**Current connected clients — announcement server (8 clients, all announcement, all on 10.1.8.21):**

| Client ID | Name | Connected |
|-----------|------|-----------|
| `snapclient-announcement-0#10` | Sunroom Announcement SC0 C3 | ✓ |
| `snapclient-announcement-1#11` | Kitchen Announcement SC1 C6 | ✓ |
| `snapclient-announcement-2#12` | Family Room Announcement SC2 C2 | ✓ |
| `snapclient-announcement-3#13` | Master Bed Announcement SC3 C5 | ✓ |
| `snapclient-announcement-4#14` | Master Bath Announcement SC4 C7 | ✓ |
| `snapclient-announcement-5#15` | Terrace Announcement SC5 C0 | ✓ |
| `snapclient-announcement-6#16` | Living Room Announcement SC6 C1 | ✓ |
| `snapclient-announcement-7#17` | Dining Room Announcement SC7 C4 | ✓ |

**Announcement server streams (current):**

| Stream name | Port | Type | Notes |
|-------------|------|------|-------|
| `Annoucements` | 4963 | TCP server PCM | Main shared announcement stream (all clients assigned here) |
| `Music Assistant - 69aa13 (announcement)` | 5141 | TCP server FLAC | Per-client MA announcement stream |
| `Music Assistant - 772b65 (announcement)` | 5034 | same | |
| `Music Assistant - 7b1c96 (announcement)` | 5000 | same | |
| `Music Assistant - 246bb7 (announcement)` | 4986 | same | |
| `Music Assistant - 410033 (announcement)` | 4966 | same | |
| `Music Assistant - ca79f6 (announcement)` | 4985 | same | |
| `Music Assistant - 4b872b (announcement)` | 5112 | same | |
| `Music Assistant - d61c77 (announcement)` | 4999 | same | |

**Implications for add-on architecture:**
1. The add-on must target **two RPC endpoints** — the main server (port 1705) for client/group discovery, and the announcement server (port 1715) for stream provisioning.
2. The existing shared `Annoucements` stream (port 4963) is NOT per-room — it broadcasts to all clients. The per-client design from the spec is the correct upgrade.
3. Music Assistant already successfully uses `Stream.AddStream` on the announcement server to create per-client streams. Our add-on will do the same.
4. The add-on's `announce_port_base` should start above 5141 (or scan for used ports) to avoid conflicts with Music Assistant streams. Recommend default `5200` rather than `4963` to avoid the existing shared stream.
5. All 8 per-room announcement clients are pre-wired and connected — the add-on's M1 client discovery will see them immediately.

**Config issues found:**
- `$idle_threshold = 10000` in main `snapserver.conf` has a `$` prefix — this is invalid syntax and is silently ignored. Should be `idle_threshold = 10000`. (Informational only; does not affect our add-on.)

---

### 3. SSH + Snapcast Host File Access — CONFIRMED

**Method:** SSH into `root@10.1.8.9` with password authentication; located config files; tested reload/restart.

**Findings:**
- SSH access: `root@10.1.8.9` with password authentication ✓
- `snapserver.conf`: `/etc/snapserver.conf` ✓ (writable by root)
- `snapserver-announcement.conf`: `/etc/snapserver-announcement.conf` ✓ (writable by root)
- `systemctl reload snapserver` → **FAILS** (`Job type reload is not applicable for unit snapserver.service`)
- `systemctl restart snapserver` → **WORKS** ✓
- Same behavior applies to `snapserver-announcement.service` (restart required, reload not supported)
- Filesystem access for file-edit mode: ✓ confirmed

**Recommendation for M1:**
- The provisioning module must use `restart` not `reload` for both snapserver instances.
- Two `systemctl restart` calls per provisioning operation (restart main server and/or announcement server as needed).
- Since `Stream.AddStream` is confirmed working, **RPC-mode stream creation is preferable** to file-edit mode for M1 — no server restart needed for adding announcement streams. File-edit mode is still needed for persistent sources that survive server restarts.

---

### 4. ESPHome `homeassistant.service` → Custom Integration Service — CONFIRMED

**Method:** (a) Inspected the production satellite YAML. (b) Created `test_echo` custom integration on HAOS. (c) Called `test_echo.ping` via REST API. (d) Verified in HA logs.

**Production satellite YAML (`/config/esphome/m5stack-atom-echo-a14640.yaml`):**

The active satellite is ALREADY calling `homeassistant.service` in production:
```yaml
on_intent_progress:
  - homeassistant.service:
      service: rest_command.stream_tts_to_snapcast
      data_template:
        url: !lambda 'return x;'

on_tts_end:
  - homeassistant.service:
      service: rest_command.stream_tts_to_snapcast
      data_template:
        url: !lambda 'return x;'
```

This is the existing TTS routing mechanism, confirmed working in production.

**`test_echo` integration test:**

Created `/config/custom_components/test_echo/__init__.py` registering `test_echo.ping`. Called via REST API:
```
POST /api/services/test_echo/ping {"message": "direct_api_call_test"}
```

HA log confirmed:
```
2026-05-13 05:54:34.616 WARNING [custom_components.test_echo] M0_TEST_ECHO_RECEIVED: message=direct_api_call_test data={'message': 'direct_api_call_test'}
```

✓ Custom integration service registered, callable, and logged.

Test integration and `test_echo:` configuration.yaml entry cleaned up after test.

**Assessment:** `homeassistant.service` from ESPHome uses the same HA service call API path as the REST API. Since `test_echo.ping` works via REST API, and the production satellite already uses `homeassistant.service` for HA services, the pattern is confirmed. No blocker.

---

## Architecture Observation: Existing Prototype

The current system has a working (but limited) prototype at `/config/stream_tts_to_snapcast.py` (running as `tts-stream-server.service` on the Snapcast LXC). It uses:
- Single shared TCP port 4963 → broadcasts TTS to ALL announcement clients simultaneously
- No per-room targeting (all rooms hear every TTS announcement)
- No group management
- No HA integration (uses `rest_command` hack)

The new add-on replaces this with per-client streams, routing via satellite mapping, and proper HA integration. This is exactly the gap the spec addresses.

---

## Recommendations Before M1

| # | Finding | Impact | Recommendation |
|---|---------|--------|----------------|
| 1 | TTS URLs are MP3 (not WAV), always via ffmpeg | Architecture | Drop PCM passthrough from M1 scope; implement ffmpeg-only path first. Add PCM detection/passthrough in M2 as optimization. |
| 2 | TLS cert is for external domain; add-on must use `verify=False` | Code | `httpx.AsyncClient(verify=False)` for TTS URL fetches; `ffmpeg -tls_verify 0` |
| 3 | Add-on targets the announcement Snapcast server only | Config schema | Single `snapcast_rpc_port` pointing at the announcement server. Music server ducking is external to the add-on. No dual-server config needed. *(Supersedes earlier dual-RPC finding.)* |
| 4 | `Stream.AddStream` confirmed in v0.35.0 | Architecture | RPC-mode stream creation is the primary mode for M1/M2. File-edit mode is secondary (for persistence). |
| 5 | `announce_port_base` default 4963 is already in use | Config | Default is `5200` to avoid conflict with existing shared `Annoucements` stream at 4963. |
| 6 | `systemctl reload` not supported; must use `restart` | Code | provisioning.py: always use `restart`, never `reload` |
| 7 | 8 announcement clients already connected and named | M1 testing | M1 client discovery will work immediately. Use existing announcement client IDs for integration testing. |

---

## Summary

All four M0 items are verified. The architecture as specified in SPEC.md is sound. The confirmed amendments for M1:

1. **Single-server target** — the add-on connects to one Snapcast server (the announcement-dedicated instance). Music server and volume ducking are outside the add-on's scope. Config has one `snapcast_rpc_port`.
2. **ffmpeg-first for M1** — skip PCM passthrough in M1 since TTS URLs are always MP3. Simplifies M1 scope significantly.
3. **TLS disabled** — TTS URL fetching always uses `verify=False`.
4. **Default port** — `announce_port_base` default is 5200 (avoids existing shared stream at 4963).

No blockers. Ready to proceed to M1 on your acceptance.
