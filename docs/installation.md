# Installation Guide

## Prerequisites

1. **Home Assistant OS** 2026.5.1 or newer
2. **Snapcast server** running on your LAN (tested on 0.27+) — this project does not install Snapcast for you
3. **Snapcast clients** already configured and connecting to your Snapcast server

## Step 1: Install the Add-on

1. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
2. Add: `https://github.com/Ctrlable/ha-ctrlable-snapcast-tts`
3. Find **Ctrlable Snapcast TTS Streamer** and click **Install**
4. Configure:
   - Set `snapcast_host` to your Snapcast server IP
   - Choose `config_mode`: `file_edit` (recommended) or `rpc_only`
   - If using `file_edit`, upload an SSH private key in the add-on's Advanced tab
5. Click **Start**

## Step 2: Configure Snapcast Clients

1. Open the **Snapcast TTS** panel in your HA sidebar
2. Go to the **Clients** tab — you'll see all Snapcast clients discovered from your server
3. Enable clients you want to use for announcements
4. For `file_edit` mode: the add-on will automatically update `snapserver.conf` and reload Snapcast
5. For `rpc_only` mode: copy the generated snippet from the **Streams** tab and paste it into `snapserver.conf`, then reload Snapcast manually

## Step 3: Install the Integration (via HACS)

1. Open HACS → **⋮ → Custom repositories**
2. Add: `https://github.com/Ctrlable/ha-ctrlable-snapcast-tts`, category: **Integration**
3. Install **Ctrlable Snapcast TTS** and restart Home Assistant
4. Go to **Settings → Devices & Services → Add Integration** → search **Ctrlable Snapcast TTS**
5. Enter:
   - **Add-on URL**: `http://localhost:8765` (if add-on is on same HAOS instance)
   - **Bearer token**: copy from the add-on's Advanced tab

## Step 4: Configure Satellite Mapping

1. In the integration's **Options** (Settings → Devices & Services → Ctrlable Snapcast TTS → Configure)
2. Add a mapping row:
   - **Satellite ID**: your ESPHome device `name` (e.g., `ctrlable-living-room`)
   - **Wake word**: `*` to match any, or a specific word (e.g., `okay_nabu`)
   - **Target client(s)**: select one or more Snapcast clients

## Step 5: Flash a Satellite

1. Copy `esphome/packages/ctrlable_atom_echo_base.yaml` to your ESPHome config directory
2. Create a per-satellite YAML:
   ```yaml
   substitutions:
     name: ctrlable-living-room        # becomes the satellite_id
     friendly_name: "Living Room"
     micro_wake_word_model: okay_nabu
   
   packages:
     ctrlable_base: !include packages/ctrlable_atom_echo_base.yaml
   ```
3. Flash to your M5Stack Atom Echo (or compatible ESP32 board)
4. Add your `secrets.yaml` entries: `wifi_ssid`, `wifi_password`, `api_encryption_key`

## Verify

Say the wake word near a satellite. The TTS response should play on the mapped Snapcast client, not on the satellite itself.
