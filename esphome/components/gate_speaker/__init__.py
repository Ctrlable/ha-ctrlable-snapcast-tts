"""gate_speaker: a speaker that either plays through another one, or swallows.

Why this exists: on the AtomS3R Echo the microphone and the speaker share one
I2S bus, and ESPHome lets only one of them hold it. A satellite that keeps its
wake word listening during an answer (so "stop" can interrupt it) can then
never start its speaker -- `Parent bus is busy` once a second -- and the local
copy of the answer never finishes. That wedged the master bath unit for a
day and a half (2026-10-12 22:01 onwards).

When the answer plays elsewhere (Snapcast, or a server satellite) the device
does not need its speaker at all, so the media player is pointed at this
instead. Passthrough ON forwards everything to `output` (Regular mode: the
answer plays here). Passthrough OFF accepts the audio at real-time pace and
drops it, never touching the bus, so the media player still sees an
announcement that lasts as long as the answer -- the stop-word arming waits on
exactly that.
"""

CODEOWNERS = []
