# Ctrlable expression set for the CoreS3 satellite

`src/` holds the original SVGs. The PNGs beside it are what the firmware uses.

**Nothing needs copying.** The config references them by raw URL from this public
repo, the same way upstream references the Casita set, so a rebuild pulls them
straight from GitHub and the HAOS VM never has to be touched:

```
https://raw.githubusercontent.com/Ctrlable/ha-ctrlable-snapcast-tts/main/esphome/images/<name>.png
```

Two consequences worth knowing. The reference tracks `main`, so replacing a PNG
here changes the display on the next rebuild — convenient, but it means a build
is not reproducible against a moving branch; pin to a commit SHA in the URL if
that matters. And a build needs internet: for an offline build, drop the PNGs in
`<esphome config dir>/images/` and change the substitutions back to
`images/<name>.png`.

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
for f in loading idle listening thinking replying alert no_wifi no_ha_file mute awake; do
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
| awake (wake word heard, mic not open yet) | `ctrlable_awake.png` |

Unused but available in `src/`: `angry`, `logo`, `powered_off`, `sleepy`,
`smiling`, `surprised`, `talking`, `timer`, `winking`.

(The source arrived as `ctrlalbe_awake.svg`; renamed on the way in.)
