# Ctrlable Snapcast TTS Streamer

HAOS add-on that streams Home Assistant TTS audio to specific Snapcast clients with low latency.

See the [main repository README](https://github.com/Ctrlable/ha-ctrlable-snapcast-tts) for full documentation.

## Planned: this add-on moves to the host

A migration plan lives in the audio-manager repo at
`docs/snapcast-streamer-migration.md`. The short version: the streamer
infers state about an audio stack it cannot see, and most of this add-on's
harder bugs came from that. Running it beside the stack removes the
inference rather than refining it. Not started.
