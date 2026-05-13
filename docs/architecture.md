# Architecture

See `SPEC.md` §2 for the full architecture overview.

This document covers operational details for advanced users.

## Why not `media_player.play_media`?

Home Assistant 2026.5.x does not preserve Piper's streaming TTS behavior through the `media_player.play_media` path — the audio is buffered before playback begins. This project uses ESPHome's `on_intent_progress` event instead, which fires with the TTS URL before the audio is fully generated.

## Data Flow

1. User speaks → satellite detects wake word → ESPHome `on_intent_progress` fires
2. ESPHome calls `ctrlable_snapcast_tts.announce` with the TTS URL and satellite ID
3. Integration resolves the satellite ID to a Snapcast client ID via the mapping
4. Integration POSTs to the add-on's `/announce` endpoint
5. Add-on acquires a per-client lock and moves the client to its announcement group
6. Add-on streams the TTS audio (PCM passthrough or ffmpeg) to the client's TCP port
7. Snapcast delivers audio to the client in real time
8. After streaming completes + buffer drain, add-on moves the client back to its home group

## Port Allocation

Each enabled Snapcast client gets a dedicated TCP port starting from `announce_port_base` (default 4963). The mapping is persisted in `/data/state.json` and survives add-on restarts.

## Snapcast Config Management

**File-edit mode**: The add-on SSHes into the Snapcast host and writes source lines between marker comments in `snapserver.conf`:
```
# >>> ctrlable managed >>>
source = tcp://0.0.0.0?name=ann_<id>&port=4963&mode=server&sampleformat=48000:16:2&codec=pcm
# <<< ctrlable managed <<<
```

**RPC-only mode**: The add-on surfaces the config snippet in the UI. The user copies it manually and confirms.
