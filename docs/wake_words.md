# Wake Words

## Built-in Models (Available Now)

The ESPHome `micro_wake_word` component has several built-in models:

| Model name | Wake phrase |
|-----------|------------|
| `okay_nabu` | "Okay Nabu" |
| `hey_jarvis` | "Hey Jarvis" |
| `hey_mycroft` | "Hey Mycroft" |
| `alexa` | "Alexa" |

Use any of these as `micro_wake_word_model` in the per-satellite YAML.

## Custom Ctrlable Wake Words

Custom models for the Ctrlable wake word slate (Maya, Luna, Nova, Apollo) are **not yet available** and are out of scope for v1. They will be added as separate deliverables once trained.

When available, they will be loaded via a custom external component or model URL in the ESPHome config.

## Wake-Word-Aware Routing (M5)

In M5, different wake words can route to different Snapcast clients from the same satellite.

Example: hallway satellite with two wake words:
- "Okay Nabu" → kitchen speaker
- "Hey Jarvis" → bedroom speaker

Configure via the integration's Options flow, adding separate mapping rows for each wake word.
