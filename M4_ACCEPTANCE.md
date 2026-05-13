# M4 Acceptance Request — Multi-zone + Robustness

**Add-on version:** `0.1.14`
**Integration version:** `0.1.0` (no changes in M4)
**Date:** 2026-05-13

---

## M4 Scope

M4 delivers multi-zone robustness, structured logging, and per-client test buttons. Most of the service-layer work (announce_multi, set_mapping, announced event, multi-target mapping) was already shipped in M2/M3.

---

## Deliverables

| Item | Status |
|------|--------|
| `POST /announce/multi` — parallel multi-client streaming | Done (M2) |
| Multi-target `target_snapclient_ids` in mapping editor | Done (M3) |
| `ctrlable_snapcast_tts.set_mapping` service | Done (M3) |
| `ctrlable_snapcast_tts_announced` event on HA bus | Done (M3) |
| JSON structured logging in add-on | Done (M4) |
| `GET /test_audio` — 440 Hz PCM test tone served by add-on | Done (M4) |
| `POST /ui/clients/{id}/test` — fires test tone through full pipeline | Done (M4) |
| **▶ Test** button per client in Clients tab UI | Done (M4) |

---

## Acceptance Criteria

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Add-on logs are JSON-formatted (ts, level, logger, msg fields) | Pending live test |
| 2 | "▶ Test" button appears in Clients tab for each client | Pending live test |
| 3 | Clicking "▶ Test" on an enabled client plays a 440 Hz tone on that speaker | Pending live test |
| 4 | Test result appears in Activity tab (format=pcm_wav, duration ~1.5s) | Pending live test |
| 5 | "▶ Test" button is disabled (greyed out) when client is not enabled | Pending live test |
| 6 | Map satellite to two snapclients; call announce with both target IDs; hear synced audio in both rooms | Pending live test |

---

## Installation Steps

1. Update the add-on: HA → Settings → Add-ons → Ctrlable Snapcast TTS Streamer → Update to v0.1.14
2. Open the add-on's Clients tab
3. Verify the new "Test" column appears
4. Click "▶ Test" on an enabled client → listen for the 440 Hz tone
5. Check Activity tab for the result entry
6. Check add-on logs (HA → Settings → Add-ons → Logs) — should be JSON lines

---

## What M4 Does NOT Include (deferred to M5)

- Wake-word-aware routing (mapping schema already has `wake_word` column; resolver is built; M5 adds the UI dropdown and multi-model ESPHome YAML)
- Sensor entities
- Prometheus metrics

---

## Request

**Please install v0.1.14 and run the acceptance steps above.**

Once all 6 criteria are confirmed, reply "M4 accepted" to unlock M5 (wake-word-aware routing).
