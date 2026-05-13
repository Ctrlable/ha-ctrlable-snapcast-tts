# M1 Acceptance Request — Add-on Skeleton

**Version shipped:** `0.1.8`
**Date:** 2026-05-13
**Tested on:** HAOS 2026.5.1, HA host `10.1.8.23`, Snapcast server `10.1.8.9:1715`

---

## M1 Scope (from SPEC.md §4)

M1 delivers the HAOS add-on skeleton: Snapcast connectivity, client discovery and persistence, per-client enable/disable with port allocation, bearer-token-gated HTTP API, ingress UI, and startup watchdog.

---

## Acceptance Criteria — All Met

| # | Criterion | Evidence |
|---|-----------|----------|
| 1 | Add-on installs and starts from GHCR | v0.1.8 images (aarch64, amd64, armv7) built and pushed; add-on updated successfully in HA |
| 2 | Connects to Snapcast on startup | Logs: `Snapcast connection established` to `10.1.8.9:1715` |
| 3 | Discovers all clients via JSON-RPC | `/snapcast/clients` returns 10 clients (2 disconnected PC clients + 8 connected announcement snapclients) |
| 4 | Ingress UI accessible | Web interface loads at HA ingress path; Connection, Clients, Activity, Advanced tabs all functional |
| 5 | Client enable/disable with port allocation | 8 clients enabled; ports 5200–5207 allocated sequentially and displayed in Clients tab |
| 6 | State persists across add-on restart | Client enabled state and port assignments survived add-on restart (confirmed by user) |
| 7 | Watchdog runs on startup | Logs: watchdog executed before HTTP server came up; 0 recovered, 0 failed on clean restart |
| 8 | Bearer-token HTTP API works | `curl -H "Authorization: Bearer <token>" http://10.1.8.23:8099/snapcast/clients` → 200 + full client list |
| 9 | Degraded mode on Snapcast unreachable | UI shows ⚠ degraded badge; API returns 503; add-on stays up |
| 10 | Multi-arch CI passes | Release workflow: aarch64 + amd64 + armv7 all green on v0.1.8 tag |

---

## Live API Output (2026-05-13)

```json
[
  {"id":"snapclient-announcement-0#10","name":"Sunroom Announcement SC0 C3","connected":true,"enabled":true,"announce_port":5200},
  {"id":"snapclient-announcement-1#11","name":"Kitchen Announcement SC1 C6","connected":true,"enabled":true,"announce_port":5201},
  {"id":"snapclient-announcement-2#12","name":"Family Room Announcement SC2 C2","connected":true,"enabled":true,"announce_port":5202},
  {"id":"snapclient-announcement-4#14","name":"Master Bath Announcement SC4 C7","connected":true,"enabled":true,"announce_port":5203},
  {"id":"snapclient-announcement-7#17","name":"Dining Room Announcement SC7 C4","connected":true,"enabled":true,"announce_port":5204},
  {"id":"snapclient-announcement-5#15","name":"Terrace Announcement SC5 C0","connected":true,"enabled":true,"announce_port":5205},
  {"id":"snapclient-announcement-6#16","name":"Living Room Announcement SC6 C1","connected":true,"enabled":true,"announce_port":5206},
  {"id":"snapclient-annoucement-3#13","name":"Master Bed Announcement SC3 C5","connected":true,"enabled":true,"announce_port":5207}
]
```

---

## What M1 Does NOT Include (deferred to M2+)

- `POST /announce` endpoint (streamer.py / ffmpeg pipeline)
- Snapcast stream provisioning (provisioning.py / snapserver.conf management)
- HA custom integration (custom_components/ctrlable_snapcast_tts/)
- ESPHome satellite YAML package
- Per-client home group detection and restoration

---

## M2 Preview

M2 delivers the streaming pipeline:

1. `streamer.py` — fetch TTS URL, detect PCM/WAV vs MP3, pipe to per-client TCP announce port via ffmpeg or passthrough
2. `provisioning.py` — generate `snapserver.conf` source lines; SSH file-edit or UI copy-paste snippet mode
3. `POST /announce` and `POST /announce/multi` endpoints
4. Snapcast group move → stream → restore logic with per-client locks
5. Home group auto-detection on first announcement

---

## Request

**Please confirm M1 acceptance** by replying "M1 accepted" (or note any remaining issues).

Once accepted, work on M2 begins immediately.
