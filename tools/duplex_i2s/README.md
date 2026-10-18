# duplex_i2s capture tools

A board running `duplex_i2s` with the `capture_audio` API action records N
seconds of three 16 kHz int16 tracks, interleaved: **mic, echo reference, AEC
output**, and sends them as one raw TCP stream.

    # receiver (e.g. on beta)
    socat -u TCP-LISTEN:7788,reuseaddr OPEN:cap.raw,creat,trunc
    # trigger from Ctrlable Pro
    esphome.<device>_capture_audio  {seconds: 25, host: <receiver ip>, port: 7788}

Analysis (needs `pymicro_wakeword` -- the LVA venv on beta has it):

    python cap_analyze.py cap.raw            # levels per 250 ms + stop-model scores
    python cap_slide.py   cap.raw            # 5-frame average (what the device decides on)

The stop model path inside the scripts is beta's
`/root/lva-next/wakewords/stop.json`.
