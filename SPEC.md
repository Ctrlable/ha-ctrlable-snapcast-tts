# Ctrlable Snapcast TTS Streamer — Specification

**Document status:** v1.0 (initial brief for Claude Code)
**Target consumer:** Claude Code, working with shell access to a HAOS 2026.5.1 instance and a Snapcast server.
**Author:** Spec produced from architectural discussion with Ron Luna (Wavesquare / Ctrlable).

---

## 1. Purpose

Build a system that routes Home Assistant Assist TTS responses from ESP32-based voice satellites to specific Snapcast clients, **with low-latency streaming**, configurable entirely from the Home Assistant UI (no per-satellite firmware changes when routing targets change), and packaged as a shippable component suitable for both personal use and commercial white-label deployment (Ctrlable / hospitality).

The fundamental constraint that shapes this design: **Home Assistant's `media_player.play_media` does not preserve streaming TTS behavior in HAOS 2026.5.1.** When Piper streams TTS output incrementally, the `play_media` path waits for completion before playback. To preserve the latency win of streaming, the architecture must trigger audio routing via the ESPHome `on_intent_progress` / `on_tts_end` events directly, not via `media_player.play_media`.

---

## 2. Architecture overview

Three artifacts, separate concerns, communicating over local HTTP:

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

**Artifact 1: HAOS Add-on (`ctrlable-snapcast-streamer`)**
- Docker container managed by HAOS Supervisor.
- Owns all Snapcast topology management and audio streaming.
- Discovers snapclients via Snapcast JSON-RPC.
- Generates per-client announcement streams and groups.
- Exposes ingress UI for setup + activity monitoring.
- HTTP API for the integration to call.

**Artifact 2: HA Custom Integration (`ctrlable_snapcast_tts`)**
- HACS-installable Python integration.
- Owns the satellite → snapclient mapping (with wake-word-aware optional column).
- Exposes the `ctrlable_snapcast_tts.announce` service.
- Config flow + options flow for UI-driven mapping setup.
- Calls the add-on's HTTP API to execute announcements.

**Artifact 3: Reference Satellite ESPHome YAML Package**
- Identical-across-satellites configuration block.
- Uses `${name}` substitution for identity.
- Calls the integration's service from `on_intent_progress` and `on_tts_end`.
- No hardcoded IPs, ports, or Snapcast knowledge.

---

## 3. Design decisions (pinned upfront — do not revisit without discussion)

These are committed; Claude Code should not redesign them.

1. **Identifier for satellites:** ESPHome device name (the `name:` field at the top of each satellite YAML). Already unique, already known to HA via the ESPHome integration, doesn't change on MAC swap.

2. **Trigger event for announcements:** ESPHome `on_intent_progress` (fast path) with `on_tts_end` as a deduplication fallback. **Not** `media_player.play_media` — HAOS 2026.5.1 doesn't preserve streaming through that path.

3. **Snapcast topology model:** One announcement stream **per snapclient** (true parallel announcements across rooms). Per-client locks; no global queue.

4. **Port allocation:** Sequential from a configurable base port (default 4963), persisted in the add-on's `/data/state.json`. First snapclient enabled gets 4963, second gets 4964, etc.

5. **Home group restoration model:** Auto-detect on first announcement, persist, allow UI override. Watchdog evicts orphaned clients from announce groups before snapshotting.

6. **Snapcast config management strategy:** Support both modes:
   - **File-edit mode** (preferred for Ctrlable deployments): add-on writes `snapserver.conf` snippets via SSH/file access to the Snapcast host.
   - **JSON-RPC + manual snippet mode** (for users who can't grant file access): add-on generates the config snippet, user copies it manually into `snapserver.conf` and reloads.
   - User picks mode in the add-on's setup UI.

7. **Audio format handling:** Inspect Content-Type on first announcement from a given HA host. PCM/WAV → direct passthrough (strip WAV header). MP3/other → ffmpeg passthrough. Cache decision per source host.

8. **Multi-zone announcements:** Temporary group consolidation (move all target clients into one announce group, play once, restore). Lock ordering by sorted client ID to prevent deadlock.

9. **Authentication:** Add-on issues a bearer token on first setup, displayed in UI for the integration to consume. Integration → add-on calls authenticated. ESPHome → integration is HA-authenticated automatically.

10. **Wake word model training:** **OUT OF SCOPE for Claude Code.** Models are sourced separately. The system consumes pre-trained `.tflite` models referenced by name. See §9.

---

## 4. The HAOS Add-on (`ctrlable-snapcast-streamer`)

### 4.1 Package structure

```
ctrlable-snapcast-streamer/
├── config.yaml              # HA add-on manifest
├── Dockerfile
├── rootfs/
│   └── etc/services.d/streamer/run
├── app/
│   ├── main.py              # FastAPI app: ingress UI + HTTP API
│   ├── snapcast.py          # JSON-RPC client + topology manager
│   ├── streamer.py          # PCM passthrough + ffmpeg fallback
│   ├── provisioning.py      # snapserver.conf generator
│   ├── watchdog.py          # startup recovery + health checks
│   ├── auth.py              # bearer token management
│   ├── state.py             # /data/state.json persistence
│   └── ui/                  # config panel (FastAPI templates or static)
├── DOCS.md
├── README.md
├── icon.png
├── logo.png
└── translations/
    └── en.yaml
```

### 4.2 `config.yaml` (add-on manifest) requirements

- `name: Ctrlable Snapcast TTS Streamer`
- `slug: ctrlable_snapcast_streamer`
- `version`: semver, starts at `0.1.0`
- `arch:` aarch64, amd64 (at minimum)
- `init: false` (use s6-overlay or run script directly)
- `ingress: true`
- `ingress_port: 8099`
- `panel_icon: mdi:speaker-wireless`
- `panel_title: Snapcast TTS`
- `homeassistant_api: true` (for future use; not required in M1)
- `ports:` expose `8765/tcp` for direct HTTP API access (so the integration can reach it; also allows the legacy `rest_command` pattern as a fallback)
- `options:` snapcast host, RPC port, announce port base, config mode (file-edit / rpc-only), SSH credentials (if file-edit), log level
- `schema:` matching options validation

### 4.3 Persistent state (`/data/state.json`)

```json
{
  "schema_version": 1,
  "snapcast": {
    "host": "10.1.8.9",
    "rpc_port": 1705,
    "config_mode": "file_edit",
    "ssh_host": "10.1.8.9",
    "ssh_user": "root",
    "ssh_key_path": "/data/ssh_key"
  },
  "auth": {
    "bearer_token": "generated-on-first-run"
  },
  "clients": {
    "ac:bc:de:01:02:03": {
      "name": "Living Room",
      "enabled": true,
      "announce_port": 4963,
      "announce_group_id": "ann_grp_ac_bc_de_01_02_03",
      "home_group_id": "grp_living_room_music",
      "home_group_autodetected": true,
      "format_cache": {
        "10.1.8.23:8123": "pcm_wav"
      }
    }
  },
  "ports_in_use": [4963, 4964]
}
```

State is the single source of truth at runtime. The UI reads from and writes to this. Reloaded on add-on restart.

### 4.4 Snapcast topology manager (`snapcast.py`)

Implements a JSON-RPC client (line-delimited JSON over TCP to port 1705). Required operations:

- `get_status()` → full server status, parsed into structured dicts.
- `list_clients()` → list of `{id, name, connected, current_group_id}`.
- `list_groups()` → list of `{id, name, stream_id, client_ids}`.
- `list_streams()` → list of `{id, uri, status}`.
- `move_client_to_group(client_id, target_group_id)` → uses `Group.SetClients` with merged client list.
- `remove_client_from_group(client_id, group_id)` → recomputed client list.
- `subscribe_events(callback)` → maintains a long-lived JSON-RPC connection for `Client.OnConnect`, `Client.OnDisconnect`, `Server.OnUpdate` notifications. Updates in-memory topology cache.
- `create_announce_group_for_client(client_id, stream_id)` → if a group bound to the announce stream doesn't exist, create it. Group ID format: `ann_grp_<client_id_normalized>`.

Error handling: every RPC call has 5s timeout, raises typed exceptions (`SnapcastTimeoutError`, `SnapcastRPCError`). All retries are explicit at the caller, not automatic.

### 4.5 Provisioning (`provisioning.py`)

Given a client enabled in state, produce:

1. A `snapserver.conf` source line:
   ```
   source = tcp://0.0.0.0?name=ann_<client_id_normalized>&port=<assigned_port>&mode=server&sampleformat=48000:16:2&codec=pcm
   ```
   `<client_id_normalized>` strips colons from MAC-form IDs.

2. The bootstrap JSON-RPC sequence to create the announce group, bind it to the stream, and add the client to it (run after Snapcast reload).

**File-edit mode:** SSH into the Snapcast host, append the line to `snapserver.conf` between marker comments (`# >>> ctrlable managed >>>` ... `# <<< ctrlable managed <<<`), reload Snapcast (try `systemctl reload snapserver`, fall back to `systemctl restart snapserver`).

**RPC-only mode:** Surface the generated config snippet in the UI for the user to copy. Block client enablement on user confirming "I've added this and reloaded Snapcast."

### 4.6 Streamer (`streamer.py`)

Core logic per announcement:

```python
async def announce(client_id: str, tts_url: str, source_host: str) -> AnnounceResult:
    state = get_state()
    client = state.clients[client_id]
    if not client.enabled:
        raise ClientNotEnabled(client_id)

    async with per_client_lock(client_id):
        # Move to announce group
        await snapcast.move_client_to_group(client_id, client.announce_group_id)
        try:
            # Resolve format strategy
            fmt = client.format_cache.get(source_host)
            if fmt is None:
                fmt = await detect_format(tts_url)
                state.clients[client_id].format_cache[source_host] = fmt
                save_state()

            # Stream
            t_start = time.monotonic()
            if fmt == "pcm_wav":
                await stream_pcm_passthrough(tts_url, client.announce_port)
            else:
                await stream_via_ffmpeg(tts_url, client.announce_port)
            stream_duration = time.monotonic() - t_start

            # Wait for Snapcast buffer drain
            await asyncio.sleep(BUFFER_DRAIN_SECONDS)  # default 1.5
        finally:
            await snapcast.move_client_to_group(client_id, client.home_group_id)

        return AnnounceResult(client_id=client_id, duration=stream_duration, format=fmt)
```

**Multi-target version** (`announce_multi`): acquires locks in sorted order, moves all targets to a single announce group (first target's), streams once, restores each to its home group.

**PCM passthrough** (`stream_pcm_passthrough`):
```python
async def stream_pcm_passthrough(tts_url: str, port: int):
    # Use httpx for async streaming HTTP
    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        async with client.stream("GET", tts_url) as resp:
            # Strip WAV header (first 44 bytes) if present
            buffer = b""
            async for chunk in resp.aiter_bytes(chunk_size=4096):
                buffer += chunk
                if len(buffer) >= 44 and not header_stripped:
                    if buffer[:4] == b"RIFF":
                        chunk_to_send = buffer[44:]
                    else:
                        chunk_to_send = buffer
                    header_stripped = True
                    # ... open TCP socket, write
```

Open the TCP socket lazily on first byte to avoid holding open sockets when TTS source is slow.

**ffmpeg fallback** (`stream_via_ffmpeg`):
```python
proc = await asyncio.create_subprocess_exec(
    "ffmpeg",
    "-tls_verify", "0",
    "-i", tts_url,
    "-f", "s16le",
    "-ar", "48000",
    "-ac", "2",
    "-",
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.DEVNULL,
)
# Pipe stdout to TCP socket
```

### 4.7 Watchdog (`watchdog.py`)

Runs **before the HTTP server starts accepting requests** on every add-on boot:

1. Connect to Snapcast.
2. For each known announce group in state, list its current clients.
3. For each such client, move it back to its home group.
4. Verify no client is currently in any announce group.
5. Log structured summary: how many recoveries performed, any failures.

If watchdog fails (Snapcast unreachable), the add-on stays in "degraded" mode — UI shows the error, HTTP API returns 503 — until the user resolves it.

### 4.8 HTTP API

```
POST /announce                   (auth required)
  body: {"client_id": "...", "url": "...", "source_host": "..."}
  → 200 {"duration": 2.34, "format": "pcm_wav"}
  → 404 if client_id not found
  → 409 if client_id not enabled
  → 503 if Snapcast unreachable

POST /announce/multi             (auth required)
  body: {"client_ids": ["...", "..."], "url": "...", "source_host": "..."}
  → 200 {...}

GET /status                      (auth required)
  → list of clients with state, format cache, last announcement

GET /snapcast/clients            (auth required)
  → live list from Snapcast (for integration's config flow)

GET /snapcast/groups             (auth required)
  → live list from Snapcast

POST /clients/{id}/enable        (auth required)
POST /clients/{id}/disable       (auth required)
POST /clients/{id}/redetect_home (auth required)
GET  /health                     (no auth, used by Supervisor)
```

### 4.9 Ingress UI

FastAPI app serving an HTML/JS UI. Doesn't need to be React — vanilla JS + HTMX or similar is fine. Tabs:

- **Connection:** Snapcast host, RPC port, config mode, SSH credentials. "Test connection" button.
- **Clients:** Auto-populated table from `/snapcast/clients`. Per-row: name, ID, current group, connected status, enable toggle, assigned announce port (read-only), home group, "Re-detect home group" button. "Test announcement" button per row that fires a known-good test TTS clip.
- **Streams:** Generated `snapserver.conf` snippet. For file-edit mode: "Apply automatically" button. For RPC-only mode: copy snippet UI + "I've applied this" confirmation button.
- **Activity:** Live tail of recent announcements (last 100). Columns: timestamp, client, URL, format, duration, success/error.
- **Advanced:** Buffer drain time, port range, log level, regenerate bearer token, raw state.json view.

---

## 5. The HA Custom Integration (`ctrlable_snapcast_tts`)

### 5.1 Package structure

```
custom_components/ctrlable_snapcast_tts/
├── __init__.py              # Setup, service registration
├── manifest.json
├── config_flow.py           # Setup + options flows
├── const.py
├── api.py                   # Async HTTP client for add-on
├── services.py              # Service handlers
├── mapping.py               # Mapping resolver
├── strings.json
├── translations/
│   └── en.json
└── services.yaml
```

### 5.2 `manifest.json`

```json
{
  "domain": "ctrlable_snapcast_tts",
  "name": "Ctrlable Snapcast TTS",
  "codeowners": ["@ronluna"],
  "config_flow": true,
  "documentation": "https://github.com/Ctrlable/ha-ctrlable-snapcast-tts",
  "iot_class": "local_polling",
  "issue_tracker": "https://github.com/Ctrlable/ha-ctrlable-snapcast-tts/issues",
  "requirements": ["httpx>=0.27"],
  "version": "0.1.0"
}
```

### 5.3 Config flow

**Step 1 — connection:**
- Add-on URL (default `http://localhost:8765` — direct TCP, not ingress, since the integration is on-host)
- Bearer token (paste from add-on UI)
- "Test connection" call → `/health` and `/snapcast/clients`. Validates token.

**Step 2 — initial mapping setup (optional, can be skipped):**
- Auto-discovered: ESPHome devices from HA's device registry that have `voice_assistant` (filter heuristic: device with `assist_satellite` entity or model matching common patterns).
- Auto-discovered: snapclients from `/snapcast/clients`.
- Optional: pre-populate one row per satellite with a "(unmapped)" target.
- User can finish setup without mapping; do it later in options flow.

**Options flow:**
- Full mapping editor: table with rows `(satellite_id, wake_word, target_snapclient_ids[], notes)`.
- `wake_word` column allows `*` (any wake word) or a specific wake word string.
- `target_snapclient_ids` is a multi-select (supports multi-zone).
- Rows can be added, edited, deleted.
- "Test row" button per row fires a test announcement to verify the chain.

### 5.4 Mapping schema

```python
# Stored in config_entry.options
{
    "schema_version": 1,
    "mappings": [
        {
            "satellite_id": "ctrlable-living-room",
            "wake_word": "*",                                  # or "hey_maya", "ok_luna", etc.
            "target_snapclient_ids": ["ac:bc:de:01:02:03"],   # list for multi-zone
            "notes": "Default mapping for living room satellite"
        },
        {
            "satellite_id": "ctrlable-hallway",
            "wake_word": "hey_maya",
            "target_snapclient_ids": ["ac:bc:de:01:02:04"],   # kitchen
            "notes": "Hey Maya from hallway → kitchen speaker"
        },
        {
            "satellite_id": "ctrlable-hallway",
            "wake_word": "hey_luna",
            "target_snapclient_ids": ["ac:bc:de:01:02:05"],   # bedroom
            "notes": "Hey Luna from hallway → bedroom speaker"
        }
    ]
}
```

### 5.5 Mapping resolver (`mapping.py`)

```python
def resolve(satellite_id: str, wake_word: str | None) -> list[str]:
    """Return ordered list of target snapclient IDs."""
    candidates = [m for m in mappings if m["satellite_id"] == satellite_id]
    if not candidates:
        raise SatelliteNotMapped(satellite_id)

    # Prefer wake-word-specific match over wildcard
    if wake_word:
        specific = [m for m in candidates if m["wake_word"] == wake_word]
        if specific:
            return specific[0]["target_snapclient_ids"]

    wildcard = [m for m in candidates if m["wake_word"] == "*"]
    if wildcard:
        return wildcard[0]["target_snapclient_ids"]

    raise NoMatchingMapping(satellite_id, wake_word)
```

### 5.6 Service: `ctrlable_snapcast_tts.announce`

```yaml
# services.yaml
announce:
  name: Announce
  description: Stream a TTS URL to the snapclient mapped to a satellite.
  fields:
    url:
      name: TTS URL
      required: true
      selector:
        text:
    satellite_id:
      name: Satellite identifier
      description: ESPHome device name of the calling satellite. Used to look up the mapping.
      required: false
      selector:
        text:
    wake_word:
      name: Wake word
      description: The wake word that triggered this announcement (if known). Used for wake-word-aware routing.
      required: false
      selector:
        text:
    target_snapclient_ids:
      name: Override target snapclient IDs
      description: Bypass the mapping; announce directly to these clients.
      required: false
      selector:
        text:
          multiple: true
```

Handler logic:
1. If `target_snapclient_ids` provided → use directly, skip mapping.
2. Else if `satellite_id` provided → resolve via mapping (using `wake_word` if given).
3. Else → error.
4. Single target → `POST /announce`. Multiple targets → `POST /announce/multi`.
5. Log result. Emit `ctrlable_snapcast_tts_announced` event on HA bus for automations.

### 5.7 Service: `ctrlable_snapcast_tts.set_mapping`

For programmatic updates from automations. Args: `satellite_id`, `wake_word`, `target_snapclient_ids`. Upserts the mapping row.

### 5.8 Sensors (v1.5, not M1)

Not in M1. Document in spec as v1.5 work.

---

## 6. Reference Satellite ESPHome YAML

### 6.1 Base package (`packages/ctrlable_atom_echo_base.yaml`)

```yaml
# Ctrlable reference satellite base package — Atom Echo variant
# Substitutions expected from the including file:
#   name: kebab-case device name (becomes the satellite_id)
#   friendly_name: human-readable name
#   micro_wake_word_model: built-in or custom model name

esphome:
  name: ${name}
  friendly_name: ${friendly_name}
  min_version: "2025.5.0"

esp32:
  board: m5stack-atom
  cpu_frequency: 240MHz
  framework:
    type: esp-idf

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  power_save_mode: NONE
  ap:
    ssid: "${friendly_name} Fb"

captive_portal:

api:
  encryption:
    key: !secret api_encryption_key

ota:
  - platform: esphome

logger:

globals:
  - id: last_tts_url
    type: std::string
    restore_value: false
    initial_value: '""'
  - id: current_wake_word
    type: std::string
    restore_value: false
    initial_value: '""'

i2s_audio:
  - id: i2s_audio_bus
    i2s_lrclk_pin: GPIO33
    i2s_bclk_pin: GPIO19

microphone:
  - platform: i2s_audio
    id: echo_microphone
    i2s_audio_id: i2s_audio_bus
    i2s_din_pin: GPIO23
    adc_type: external
    pdm: true
    sample_rate: 16000
    correct_dc_offset: true

speaker:
  - platform: i2s_audio
    id: dummy_speaker
    i2s_audio_id: i2s_audio_bus
    i2s_dout_pin: GPIO22
    dac_type: external
    bits_per_sample: 16bit
    sample_rate: 16000
    channel: mono

media_player:
  - platform: speaker
    id: tts_media_player
    name: "TTS Output"
    internal: true
    announcement_pipeline:
      speaker: dummy_speaker
      format: MP3

micro_wake_word:
  id: mww
  vad:
  models:
    - model: ${micro_wake_word_model}
      id: wake_model
  on_wake_word_detected:
    - lambda: 'id(current_wake_word) = wake_word;'
    - voice_assistant.start:
        wake_word: !lambda return wake_word;

switch:
  - platform: template
    id: mute_switch
    name: "Mute"
    icon: "mdi:microphone-off"
    optimistic: true
    restore_mode: RESTORE_DEFAULT_OFF
    on_turn_on:
      - micro_wake_word.stop:
      - light.turn_on:
          id: status_led
          red: 100%
          green: 0%
          blue: 0%
          brightness: 30%
    on_turn_off:
      - light.turn_off: status_led
      - micro_wake_word.start:

light:
  - platform: esp32_rmt_led_strip
    id: status_led
    name: "Status LED"
    internal: true
    rgb_order: GRB
    pin: GPIO27
    num_leds: 1
    chipset: SK6812

binary_sensor:
  - platform: gpio
    pin:
      number: GPIO39
      inverted: true
    name: "Wake button"
    on_press:
      - if:
          condition:
            switch.is_off: mute_switch
          then:
            - micro_wake_word.stop:
            - voice_assistant.start:

voice_assistant:
  id: va
  microphone:
    microphone: echo_microphone
    gain_factor: 4
  micro_wake_word: mww
  use_wake_word: false
  media_player: tts_media_player
  noise_suppression_level: 2
  auto_gain: 31dBFS
  volume_multiplier: 4.0
  conversation_timeout: 40s

  on_intent_progress:
    - if:
        condition:
          lambda: 'return !x.empty();'
        then:
          - lambda: 'id(last_tts_url) = x;'
          - media_player.stop:
              id: tts_media_player
          - homeassistant.service:
              service: ctrlable_snapcast_tts.announce
              data:
                url: !lambda 'return x;'
                satellite_id: "${name}"
                wake_word: !lambda 'return id(current_wake_word);'

  on_tts_end:
    - if:
        condition:
          lambda: 'return !x.empty() && x != id(last_tts_url);'
        then:
          - lambda: 'id(last_tts_url) = x;'
          - homeassistant.service:
              service: ctrlable_snapcast_tts.announce
              data:
                url: !lambda 'return x;'
                satellite_id: "${name}"
                wake_word: !lambda 'return id(current_wake_word);'

  on_listening:
    - light.turn_on:
        id: status_led
        red: 0%
        green: 0%
        blue: 100%
        brightness: 80%

  on_intent_start:
    - light.turn_on:
        id: status_led
        red: 100%
        green: 60%
        blue: 0%
        brightness: 80%

  on_end:
    - lambda: 'id(last_tts_url) = "";'
    - lambda: 'id(current_wake_word) = "";'
    - light.turn_on:
        id: status_led
        red: 0%
        green: 100%
        blue: 0%
        brightness: 40%
    - delay: 1s
    - light.turn_off: status_led
    - if:
        condition:
          switch.is_off: mute_switch
        then:
          - micro_wake_word.start:

  on_error:
    - lambda: 'id(last_tts_url) = "";'
    - lambda: 'id(current_wake_word) = "";'
    - light.turn_on:
        id: status_led
        red: 100%
        green: 0%
        blue: 0%
        brightness: 80%
    - delay: 2s
    - light.turn_off: status_led
    - if:
        condition:
          switch.is_off: mute_switch
        then:
          - micro_wake_word.start:

  on_client_connected:
    - if:
        condition:
          switch.is_off: mute_switch
        then:
          - micro_wake_word.start:

  on_client_disconnected:
    - micro_wake_word.stop:
    - voice_assistant.stop:
```

### 6.2 Per-satellite YAML (example)

```yaml
# living_room_satellite.yaml — entire file content
substitutions:
  name: ctrlable-living-room
  friendly_name: "Ctrlable Living Room"
  micro_wake_word_model: okay_nabu

packages:
  ctrlable_base: !include packages/ctrlable_atom_echo_base.yaml
```

Three substitutions per satellite. The `name` value becomes the `satellite_id` used in mappings.

---

## 7. Milestones

Each milestone is end-to-end testable on Ron's server. Do not move to the next milestone without explicit acceptance.

### M0 — Verification (before any production code)

Confirm the following on the target HAOS instance:

1. **Verify the current TTS URL format from Piper.** Trigger a long TTS response. Capture the URL passed to `on_intent_progress`. `curl -I` to get Content-Type. Determine if PCM passthrough is viable from day one.
2. **Verify Snapcast version + RPC capabilities.** Connect to RPC port, list streams, confirm whether `Stream.AddStream` is available (informational only — we're using file-edit mode anyway).
3. **Verify SSH-from-add-on-to-Snapcast-host setup.** Confirm Snapcast runs on `10.1.8.9` LXC, locate `snapserver.conf`, confirm reload mechanism (`systemctl reload snapserver` works? or restart?).
4. **Verify ESPHome `homeassistant.service` can call custom integration services.** Build a minimal throwaway test integration that registers `test.echo`, fire it from an ESPHome `homeassistant.service` action, confirm it lands.

**Deliverable:** M0_VERIFICATION.md report documenting findings, with recommendations on:
- PCM passthrough viability for M1.
- File-edit mode preconditions met.
- Any blockers for the architecture as specified.

If any of (1), (3), or (4) reveals a blocker, **stop and discuss with Ron before proceeding to M1.**

### M1 — Add-on skeleton + Snapcast discovery

Build:
- Add-on `config.yaml`, Dockerfile, run script.
- Snapcast JSON-RPC client with `get_status`, `list_clients`, `list_groups`, `list_streams`, event subscription.
- Persistent state file at `/data/state.json` with schema versioning.
- Bearer token generation + auth middleware.
- Ingress UI: Connection tab + Clients tab (read-only, just shows discovered clients).
- HTTP endpoints: `/health`, `/snapcast/clients`, `/snapcast/groups`.
- Watchdog stub (logs "no announce groups configured yet").

**Acceptance:** Install add-on on Ron's HAOS, point it at his Snapcast, see all his clients in the Clients tab with live status. Ingress panel accessible from HA sidebar.

### M2 — Single-client announcement path

Build:
- Provisioning module: generate `snapserver.conf` snippet, manage marker comments, SSH application logic.
- Per-client port allocation, announce group creation via JSON-RPC after Snapcast reload.
- Streamer module: PCM passthrough + ffmpeg fallback, format detection + cache.
- `/announce` endpoint with full move-stream-restore logic, per-client locks.
- Watchdog: full eviction on startup.
- Clients tab: enable/disable toggle, "Re-detect home group" button, "Test announcement" button.
- Activity tab: live log of announcements.

**Acceptance:** Enable one snapclient, fire test announcement from UI, hear audio play on that client without disrupting music on other clients. Verify with `curl POST /announce` from command line. Verify watchdog by killing the add-on mid-announcement and confirming client returns to home group on restart.

### M3 — HA Custom Integration + reference satellite

Build:
- Custom integration package, manifest, config flow (Step 1 only — connection).
- API client to add-on with auth.
- `ctrlable_snapcast_tts.announce` service implementation.
- Mapping schema (in config_entry.options), basic resolver (wildcard only for M3).
- Options flow with mapping editor (single wake_word=* row per satellite for M3).
- Reference ESPHome YAML package.
- Documentation: how to flash a satellite with the reference YAML.

**Acceptance:** Flash one Atom Echo with reference YAML. Map it to a snapclient via the integration's options flow. Say "okay nabu, what time is it" — hear response stream on the mapped snapclient. End-to-end working without the legacy REST command pattern.

### M4 — Multi-zone + robustness

Build:
- `/announce/multi` endpoint with multi-target group consolidation + lock ordering.
- Mapping editor extended to support multiple target_snapclient_ids per row.
- `ctrlable_snapcast_tts.set_mapping` service.
- Service emits `ctrlable_snapcast_tts_announced` event.
- Stress test: 5 simultaneous announcements to 5 different clients, verify all complete within announcement-duration time, not 5× that.
- Structured logging throughout (JSON logs in add-on).
- Test row buttons in mapping editor.

**Acceptance:** Map satellite to two snapclients, fire announcement, hear synced audio in both rooms. Stress test passes.

### M5 — Wake-word-aware routing

Build:
- Mapping schema extended with `wake_word` column (already in M3 schema, just unused).
- Resolver prefers specific wake word over wildcard.
- Mapping editor surfaces wake_word dropdown.
- Reference YAML extended to support 2-3 simultaneous wake word models (substitution-list pattern).

**Acceptance:** Flash satellite with two wake words ("okay_nabu" + "hey_jarvis"). Configure different snapclient targets per wake word. Verify each wake word routes correctly.

### M6 (later, not v1) — Polish

- Sensor entities (current target per satellite, current state per snapclient).
- Prometheus metrics endpoint on add-on.
- Backup/restore for state.json.
- Bulk operations in UI (enable all, disable all).
- Connection resilience improvements.
- Diagnostics download.

---

## 8. Open questions for Ron (resolve before / during M0)

1. **Snapcast host file access:** Are you OK with the add-on having SSH access to the Snapcast LXC (file-edit mode)? If yes, provide SSH key setup. If preference is RPC-only mode (you copy-paste config snippets), state that.
2. **Snapcast version on the LXC:** What version are you running? (`snapserver --version`)
3. **HA TTS engine in use:** Piper local, HA Cloud TTS, or something else? This affects M0 verification step 1.
4. **HAOS access for Claude Code:** SSH or just Supervisor add-on installation via local UI? Document the workflow for Claude Code to deploy iterations.
5. **Repository structure:** One repo with both add-on and integration in subdirs (monorepo), or two separate repos? Recommend monorepo at `github.com/Ctrlable/ha-ctrlable-snapcast-tts` with `addon/` and `custom_components/` subdirs.
6. **Bearer token rotation strategy:** OK with manual rotation via UI button in v1, or want automatic rotation?

---

## 9. Wake word models — explicit out-of-scope policy

The system is designed to consume pre-trained `micro_wake_word` `.tflite` models. The `${micro_wake_word_model}` substitution in the reference YAML takes any model name — built-in (`okay_nabu`, `hey_jarvis`, `hey_mycroft`, `alexa`) or custom (`hey_maya`, `ok_maya`, `hey_luna`, etc.).

**Claude Code does not train, generate, or fabricate wake word models.** If models for the Ctrlable wake word slate (Maya, Luna, Nova, Apollo) aren't yet trained, M5 functionality can be validated using built-in models. Custom models drop in later as separate deliverables.

The wake word training workstream runs in parallel and is owned by Ron. Three possible paths (Ron's choice):
- **microWakeWord training pipeline** (DIY, open-source, requires training data).
- **Picovoice Porcupine** (commercial, requires licensing, ESPHome integration available).
- **Contracted training** (third party trains models on Ron's behalf).

Model files, when available, are loaded into ESPHome via either the built-in `micro_wake_word` external component repository or a custom external component pointing to model URLs hosted on a Ctrlable-controlled location.

---

## 10. Out of scope for v1

To prevent scope creep, the following are explicitly **not** v1 deliverables:

- Wake word model training (see §9).
- Media player entities exposing snapclients (use the `announce` service instead in v1).
- iOS / Android companion app integration (Ctrlable HA mobile app handles this separately).
- Snapcast server provisioning/installation (assume pre-installed).
- Non-Snapcast audio backends (Sonos, Chromecast, AirPlay) — separate routing systems, separate spec.
- Voice-activated music playback ("play jazz in the kitchen") — that's Music Assistant's territory.
- Multi-language UI (English only for v1, translations directory scaffolded for future).
- HA Container support (HAOS only — add-on architecture requires Supervisor).

---

## 11. Coding standards

- **Python:** 3.11+, type hints throughout, `ruff` + `mypy` clean.
- **Async:** `asyncio` everywhere in add-on. `httpx` for HTTP, not `requests`. No blocking I/O in request handlers.
- **Logging:** structured JSON logs in add-on (use `structlog` or stdlib JSON formatter). HA integration uses HA's standard logger.
- **Tests:** pytest, async-aware. Aim for >70% coverage on `snapcast.py`, `streamer.py`, `mapping.py`. UI and integration tests can be lighter.
- **Commit messages:** conventional commits (`feat:`, `fix:`, `refactor:`, etc.).
- **Versioning:** semver. Bump on every milestone acceptance.

---

## 12. Repository layout (recommended)

```
github.com/Ctrlable/ha-ctrlable-snapcast-tts/
├── README.md
├── SPEC.md                     # this document
├── LICENSE                     # Apache 2.0
├── addon/
│   └── ctrlable-snapcast-streamer/
│       ├── config.yaml
│       ├── Dockerfile
│       ├── app/
│       ├── rootfs/
│       └── DOCS.md
├── custom_components/
│   └── ctrlable_snapcast_tts/
│       └── (HA integration code)
├── esphome/
│   └── packages/
│       ├── ctrlable_atom_echo_base.yaml
│       └── examples/
│           ├── living_room.yaml
│           └── kitchen.yaml
├── docs/
│   ├── installation.md
│   ├── architecture.md
│   ├── troubleshooting.md
│   └── wake_words.md
└── .github/
    └── workflows/
        ├── lint.yml
        ├── test.yml
        └── release.yml
```

For HACS to find the integration: repository root must contain `hacs.json` describing the integration. For the add-on repository (separate or same), `repository.yaml` at root.

If splitting repos:
- `github.com/Ctrlable/ha-ctrlable-snapcast-tts` → integration
- `github.com/Ctrlable/ctrlable-snapcast-streamer-addon` → add-on

Recommendation: **start as monorepo**, split later if release cadences diverge.

---

## 13. Success criteria for v1

A non-Ron user (someone in the Ctrlable QA team, or a HA community member) can:

1. Install the add-on from a custom add-on repo URL.
2. Install the integration from HACS using a custom repo URL.
3. Configure the add-on (point at Snapcast, paste bearer token).
4. Configure the integration (point at add-on).
5. Enable a snapclient in the add-on UI.
6. Map a satellite (already flashed with reference YAML) to that snapclient in the integration UI.
7. Speak to the satellite and hear the response on the chosen snapclient.

All without editing any YAML except the per-satellite ESPHome file (which is 3 lines of substitutions).

End of spec.
