"""aec_mic: echo-cancel a microphone against what the ROOM is playing.

In Snapcast mode a satellite's answers -- and the room's music -- come out of
the room's speakers, not the device, so there is no local speaker signal to
cancel against. The server knows exactly what the room plays (its zone bus),
so it streams that mix back to the device as RTP (L16/16000/1, the same
sender ctrlable-netmic-session uses for the XVF3800), and this component runs
ESP-SR's AEC on the device's own raw mic with that stream as the reference.

The reference is held back by a fixed, adjustable delay so it lines up with
the moment the mic hears the speakers (rtp_mic_core::RefJitterBuffer: fixed
playout point, late packets become silence, slow drift trim). With no stream
the reference is silence and the mic passes through unchanged.

Wraps an existing microphone; nothing about the I2S setup changes.
"""

CODEOWNERS = []
DEPENDENCIES = ["rtp_mic"]
