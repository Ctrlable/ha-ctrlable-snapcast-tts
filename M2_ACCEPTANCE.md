# M2 Acceptance Request — Streaming Pipeline

**Version shipped:** `0.1.12`
**Date:** 2026-05-13
**Tested on:** HAOS 2026.5.1, HA host `10.1.8.23`, Snapcast server `10.1.8.9`

---

## M2 Scope

M2 delivers the audio streaming pipeline: TTS URL → ffmpeg/PCM passthrough → Snapcast TCP source, Snapcast stream discovery and client linking, `POST /announce` and `POST /announce/multi` HTTP endpoints, home group auto-detection, and the Streams provisioning UI tab.

---

## Acceptance Criteria — All Met

| # | Criterion | Evidence |
|---|-----------|----------|
| 1 | `POST /announce` streams TTS audio to a Snapcast client | Returned `{"duration":0.749,"format":"other"}` — audio heard in speakers |
| 2 | `POST /announce/multi` streams to multiple clients in parallel | Both `snapclient-announcement-6#16` and `snapclient-announcement-0#10` returned in 0.378s each |
| 3 | ffmpeg fallback handles MP3/non-PCM TTS output | Piper TTS produces MP3; ffmpeg decoded → s16le 48kHz stereo → TCP; played correctly |
| 4 | Format detected and cached per source host | `format_cache: {"10.1.8.23": "other"}` persisted in state after first announce |
| 5 | Home group auto-detected on first announcement | `home_group_id` populated from client's current Snapcast group on first call |
| 6 | Provisioning scan adopts existing TCP streams | Strategy 1 detected pre-existing TCP streams; updated `announce_port` to actual ports (4963/5206) |
| 7 | Streams UI tab shows snippet and scan results | All 8 clients show ✓ Linked after scan |
| 8 | Auto-reconnect on dropped RPC connection | `_call()` triggers `reconnect()` transparently; no more "Not connected" errors |
| 9 | All 3 CI architectures build clean | aarch64 + amd64 + armv7 green on v0.1.12 tag |

---

## Live Test Output (2026-05-13)

```
# Living Room (port 5206 — independent stream)
POST /announce {"client_id":"snapclient-announcement-6#16","url":"<piper_mp3>","source_host":"10.1.8.23"}
→ {"duration":0.104,"format":"other"}  ✓ audio heard

# All 7 rooms (port 4963 — shared stream)
POST /announce {"client_id":"snapclient-announcement-0#10","url":"<piper_mp3>","source_host":"10.1.8.23"}
→ {"duration":0.749,"format":"other"}  ✓ audio heard

# Both streams simultaneously
POST /announce/multi {"client_ids":["snapclient-announcement-6#16","snapclient-announcement-0#10"],...}
→ [{"client_id":"snapclient-announcement-6#16","duration":0.378,"format":"other"},
   {"client_id":"snapclient-announcement-0#10","duration":0.378,"format":"other"}]  ✓
```

---

## Topology Note

The live Snapcast announcement server has **7 clients sharing port 4963** (one broadcast stream) and **1 client on port 5206** (Living Room, independent). The scan adopted this pre-existing topology — no snapserver.conf changes were required. Announcing to any of the 7 clients on port 4963 plays audio in all 7 rooms simultaneously.

---

## What M2 Does NOT Include (deferred to M3+)

- SSH file-edit mode for snapserver.conf (provisioning.py stub; UI shows snippet for manual copy)
- HA custom integration (`custom_components/ctrlable_snapcast_tts/`)
- ESPHome satellite YAML package
- Per-client "Test announcement" button in the Clients UI tab

---

## M3 Preview

M3 delivers the HA custom integration:

1. `custom_components/ctrlable_snapcast_tts/` — HACS-installable integration
2. Config flow: add-on URL + bearer token + client discovery
3. `ctrlable_snapcast_tts.announce` service with satellite→client mapping
4. ESPHome `on_intent_progress` / `on_tts_end` service call triggers
5. Options flow for UI-driven mapping management

---

## Request

**Please confirm M2 acceptance** by replying "M2 accepted".

Once accepted, work on M3 (HA custom integration) begins.
