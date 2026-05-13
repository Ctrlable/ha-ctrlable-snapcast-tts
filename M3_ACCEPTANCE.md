# M3 Acceptance Request — HA Custom Integration

**Version:** `0.1.0`
**Date:** 2026-05-13
**Integration domain:** `ctrlable_snapcast_tts`

---

## M3 Scope

M3 delivers the HACS-installable Home Assistant custom integration that bridges HA Assist TTS audio to Snapcast clients via the M2 add-on API.

---

## Deliverables

| File | Purpose |
|------|---------|
| `__init__.py` | `async_setup_entry` / `async_unload_entry`, service registration |
| `api.py` | `AddonApiClient` — HTTP wrapper for add-on `/announce`, `/health`, `/snapcast/clients` |
| `config_flow.py` | Step 1: URL + bearer token (tested via `/health`); options flow: add/remove mapping menu |
| `const.py` | `DOMAIN`, `CONF_ADDON_URL`, `CONF_BEARER_TOKEN`, `EVENT_ANNOUNCED` |
| `mapping.py` | `resolve()`, `upsert()`, `remove()`, `label()` — wake-word-aware routing logic |
| `services.py` | `handle_announce`, `handle_set_mapping` — service call handlers |
| `services.yaml` | HA service UI descriptors for both services |
| `strings.json` + `translations/en.json` | Config + options flow UI strings |
| `manifest.json` | v0.1.0, `httpx>=0.27` requirement |

---

## Acceptance Criteria

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Integration loads via HACS / manual install in `custom_components/` | Pending live test |
| 2 | Config flow accepts add-on URL + bearer token, tests via `/health`, creates entry | Pending live test |
| 3 | `ctrlable_snapcast_tts.announce` with `target_snapclient_ids` calls add-on and fires event | Pending live test |
| 4 | `ctrlable_snapcast_tts.announce` with `satellite_id` + `wake_word` resolves mapping and announces | Pending live test |
| 5 | Options flow menu lets user add a satellite→client mapping; persisted across reload | Pending live test |
| 6 | Options flow menu lets user remove a mapping | Pending live test |
| 7 | `ctrlable_snapcast_tts.set_mapping` service upserts a mapping programmatically | Pending live test |
| 8 | `ctrlable_snapcast_tts_announced` event fired with url, satellite_id, wake_word, results | Pending live test |
| 9 | Unloading entry removes services cleanly | Pending live test |

---

## Installation Steps (for live test)

1. Copy `custom_components/ctrlable_snapcast_tts/` to HA `config/custom_components/`
2. Restart HA
3. Settings → Integrations → Add Integration → search "Ctrlable Snapcast TTS"
4. Enter add-on URL (`http://10.1.8.23:8099`) and bearer token
5. Verify integration card appears; click Configure to open options flow
6. Add a mapping: satellite_id = `living_room_satellite`, wake_word = `*`, target = Living Room client
7. Call `ctrlable_snapcast_tts.announce` with a TTS URL and `satellite_id: living_room_satellite`
8. Verify audio heard + `ctrlable_snapcast_tts_announced` event in HA logbook

---

## What M3 Does NOT Include (deferred to M4+)

- ESPHome satellite YAML package (`esphome/` scaffold exists but is empty)
- Per-client "Test" button in HA UI
- Sensor entities for connection status

---

## Request

**Please install the integration and run the live test steps above.**

Once all 9 criteria are confirmed, reply "M3 accepted" to unlock M4 (ESPHome satellite package).
