# Ctrlable expression set for the CoreS3 satellite

`src/` holds the original SVGs. The PNGs beside it are what the firmware
actually uses — **copy them to `<your esphome config dir>/images/`** so
`file: images/ctrlable_*.png` resolves at compile time.

## Why PNG and not the SVGs directly

ESPHome can rasterise SVG, but these needed layout work that `resize:` cannot do:

* **Aspect.** The sources are ~240–280 × 230; the panel is 320 × 240. A straight
  `resize: 320x240` stretches them. Each PNG is the art scaled proportionally and
  centred on a 320 × 240 transparent canvas, so `resize` becomes a no-op.
* **Height.** Art is 190px tall, not 240. The `thinking` and `replying` pages draw
  white text boxes at y=20..50 and y=190..220 — full-height art gets covered by them.
* **Antialiasing.** Rendered at 2× and downsampled with Lanczos; the 3px strokes
  alias badly if rasterised straight to target size.

## Backgrounds are black, deliberately

The art is Ctrlable cyan `#00b3cd` **with `#ffffff` detail** (eyes, mouth,
highlights). Upstream fills a WHITE ground for listening/thinking/replying, which
makes those white parts vanish. All six grounds are black now.

## Re-rendering

```sh
for f in loading idle listening thinking replying alert no_wifi no_ha_file mute; do
    rsvg-convert -h 440 "src/ctrlable_$f.svg" -o "/tmp/$f-2x.png"
done
# then scale each to 190px tall and centre on a 320x240 transparent canvas
```

## Mapping

| phase | file |
|---|---|
| loading / initialising | `ctrlable_loading.png` |
| idle | `ctrlable_idle.png` |
| listening | `ctrlable_listening.png` |
| thinking | `ctrlable_thinking.png` |
| replying | `ctrlable_replying.png` |
| error | `ctrlable_alert.png` |
| no wifi | `ctrlable_no_wifi.png` |
| no home assistant | `ctrlable_no_ha_file.png` |
| muted | `ctrlable_mute.png` |

Unused but available in `src/`: `angry`, `awake`, `logo`, `powered_off`, `sleepy`,
`smiling`, `surprised`, `talking`, `timer`, `winking`.
