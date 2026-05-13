# Ctrlable Snapcast TTS Streamer

Route Home Assistant Assist TTS responses to specific Snapcast clients — with low latency, per-room targeting, and zero firmware changes when routing changes.

> **Status:** Active development — M0 verification in progress. Not yet production-ready.

---

## Why

Home Assistant's `media_player.play_media` does not preserve streaming TTS behavior in HAOS 2026.5.x. When Piper streams TTS output incrementally, the standard path waits for the full audio to complete before playback begins, adding noticeable lag. This project routes TTS directly from ESPHome's `on_intent_progress` event into Snapcast clients — bypassing `media_player.play_media` entirely — so the first audio byte plays before the last is generated.

Additional pain points this solves:
- Music Assistant announcement routing requires per-satellite configuration that breaks when speakers move.
- ESPHome vanilla TTS plays back on the satellite itself, not on whole-room speakers.
- Changing which room a voice command targets normally requires firmware reflashes.

---

## Architecture

```
┌─────────────────────┐      ┌──────────────────────┐      ┌─────────────────────┐
│  ESPHome Satellite  │      │  HA Custom Integ.    │      │  HAOS Add-on        │
│  (Atom Echo etc.)   │      │  ctrlable_snapcast_  │      │  ctrlable-snapcast- │
│                     │      │  tts                 │      │  streamer           │
│  on_intent_progress │──┬──▶│                      │      │                     │
│  fires homeassistant│  │   │  Service:            │      │  HTTP endpoint:     │
│  .service call with │  │   │  .announce          │─────▶│  POST /announce     │
│  url + satellite_id │  │   │  Owns mapping:       │      │                     │
│  + wake_word        │  │   │  (sat_id, wake) →    │      │  Snapcast JSON-RPC  │
│                     │  │   │  target snapclient   │      │  topology mgmt      │
│  on_tts_end (fallbk)│──┘   │  + UI config flow    │      │                     │
└─────────────────────┘      └──────────────────────┘      │  Per-client lock    │
                                                            │  Stream PCM/MP3 to  │
                                                            │  Snapcast TCP port  │
                                                            │  Move client to    │
                                                            │  announce group,    │
                                                            │  play, restore     │
                                                            └─────────┬───────────┘
                                                                      │
                                                            ┌─────────▼───────────┐
                                                            │  Snapcast Server    │
                                                            │  (existing LXC)     │
                                                            │  Per-client streams │
                                                            │  + announce groups  │
                                                            └─────────────────────┘
```

Three components, one URL to install both:

| Component | What it does |
|-----------|-------------|
| **HAOS Add-on** (`ctrlable-snapcast-streamer`) | Manages Snapcast topology, streams audio, exposes HTTP API + ingress UI |
| **HA Integration** (`ctrlable_snapcast_tts`) | Owns satellite→snapclient mapping, exposes `announce` service, config UI |
| **ESPHome YAML Package** | Reference satellite config (3 substitution lines per device) |

---

## Installation

### Install the Add-on

1. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
2. Paste: `https://github.com/Ctrlable/ha-ctrlable-snapcast-tts`
3. Find **Ctrlable Snapcast TTS Streamer** in the store and install it.
4. Configure the add-on with your Snapcast server address, then start it.
5. Copy the bearer token from the add-on's ingress UI.

### Install the Integration (via HACS)

1. In HACS: **⋮ → Custom repositories**
2. Paste: `https://github.com/Ctrlable/ha-ctrlable-snapcast-tts`, category: **Integration**
3. Install **Ctrlable Snapcast TTS**
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** → search "Ctrlable Snapcast TTS"
6. Enter the add-on URL (`http://localhost:8765`) and bearer token from step above.

Both use the same repository URL. One URL, two installation flows.

---

## Configuration

See [docs/installation.md](docs/installation.md) for full setup instructions including:
- Snapcast server prerequisites
- SSH access setup (for file-edit mode)
- Satellite ESPHome YAML
- Mapping configuration

---

## Flash a Satellite

Create a per-satellite YAML (3 lines of substitutions):

```yaml
# living_room_satellite.yaml
substitutions:
  name: ctrlable-living-room
  friendly_name: "Ctrlable Living Room"
  micro_wake_word_model: okay_nabu

packages:
  ctrlable_base: !include packages/ctrlable_atom_echo_base.yaml
```

The `name` field becomes the `satellite_id` used in routing mappings. See [esphome/packages/](esphome/packages/) for the base package and examples.

---

## Compared to Alternatives

| Feature | This project | Music Assistant | Vanilla ESPHome TTS |
|---------|-------------|----------------|---------------------|
| Low-latency streaming TTS | Yes (bypasses play_media) | No (buffers) | No (buffers) |
| Route to Snapcast clients | Yes | No | No |
| Change routing without reflash | Yes (UI mapping) | N/A | No |
| Multi-room simultaneous | Yes (M4) | Via MA rooms | No |
| Works without Music Assistant | Yes | Required | Yes |

---

## Compatibility

| Component | Minimum version |
|-----------|----------------|
| Home Assistant OS | 2026.5.1 |
| Home Assistant | 2026.5.0 |
| Snapcast server | 0.27+ |
| ESPHome | 2025.5.0 |
| ESP32 board | M5Stack Atom Echo, any compatible |

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

Built by [Ctrlable](https://ctrlable.com) — smart building automation for hospitality and commercial spaces.
