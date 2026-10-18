# Analyse a duplex_i2s capture: interleaved int16 x3 at 16 kHz (mic, ref, aec_out).
import sys, numpy as np
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
raw = np.frombuffer(open(sys.argv[1], "rb").read(), dtype=np.int16).reshape(-1, 3).astype(np.float32)
mic, ref, out = raw[:, 0], raw[:, 1], raw[:, 2]
sr = 16000
def db(x): return 10 * np.log10(np.mean(x * x) / 32768**2 + 1e-12)
print("t(s)   mic   ref   out")
for i in range(0, len(mic), sr // 4):
    s = slice(i, i + sr // 4)
    print(f"{i/sr:5.2f} {db(mic[s]):5.0f} {db(ref[s]):5.0f} {db(out[s]):5.0f}")

def run(sig, gain, name):
    x = np.clip(sig * gain, -32768, 32767).astype(np.int16)
    m = MicroWakeWord.from_config("/root/lva-next/wakewords/stop.json")
    f = MicroWakeWordFeatures()
    probs = []
    step = 160
    for i in range(0, len(x) - step, step):
        for feat in f.process_streaming(x[i:i + step].tobytes()):
            p = m.process_streaming_prob(feat)
            if p is not None:
                probs.append((i / sr, p))
    hits = [(t, p) for t, p in probs if p > 0.5]
    best = sorted(probs, key=lambda tp: -tp[1])[:5]
    print(f"{name:22s} frames>0.5: {len(hits):3d}  top: " + ", ".join(f"{t:.2f}s={p:.2f}" for t, p in best))
print()
for g in (8, 16, 32):
    run(out, g, f"aec_out x{g}")
for g in (8, 16):
    run(mic, g, f"raw mic x{g}")
