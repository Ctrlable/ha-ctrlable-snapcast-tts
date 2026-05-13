# Ctrlable Snapcast TTS Streamer — Add-on Documentation

## Overview

This add-on manages Snapcast topology and streams Home Assistant TTS audio to specific Snapcast clients. It works in conjunction with the **Ctrlable Snapcast TTS** integration.

## Prerequisites

- A running Snapcast server (tested on 0.27+)
- Snapcast server accessible from your HAOS instance on the local network
- (Optional, for file-edit mode) SSH access from this add-on to the Snapcast host

## Configuration

| Option | Description | Default |
|--------|-------------|---------|
| `snapcast_host` | IP or hostname of your Snapcast server | `10.1.8.9` |
| `snapcast_rpc_port` | Snapcast JSON-RPC port | `1705` |
| `announce_port_base` | First TCP port to use for announcement streams | `4963` |
| `config_mode` | `file_edit` (auto) or `rpc_only` (manual snippet) | `file_edit` |
| `log_level` | Logging verbosity | `info` |

## Setup Flow

1. Install and start the add-on.
2. Open the panel (**Snapcast TTS** in your HA sidebar).
3. On the **Connection** tab, verify connectivity to your Snapcast server.
4. Copy the bearer token from the **Advanced** tab.
5. Install and configure the **Ctrlable Snapcast TTS** integration using this token.
6. On the **Clients** tab, enable the Snapcast clients you want to target for announcements.

## Support

- [GitHub Issues](https://github.com/Ctrlable/ha-ctrlable-snapcast-tts/issues)
- [Full documentation](https://github.com/Ctrlable/ha-ctrlable-snapcast-tts/blob/main/docs/installation.md)
