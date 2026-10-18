# Sliding-average (5 frames, as micro_wake_word decides) of the stop model on the AEC output.
import sys, numpy as np, contextlib, io
from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
raw = np.frombuffer(open(sys.argv[1], "rb").read(), dtype=np.int16).reshape(-1, 3)
x = raw[:, 2].copy()
with contextlib.redirect_stdout(io.StringIO()):
    m = MicroWakeWord.from_config("/root/lva-next/wakewords/stop.json")
f = MicroWakeWordFeatures()
probs = []
for i in range(0, len(x) - 160, 160):
    for feat in f.process_streaming(x[i:i + 160].tobytes()):
        p = m.process_streaming_prob(feat)
        if p is not None:
            probs.append((i / 16000, p))
t = np.array([a for a, _ in probs]); p = np.array([b for _, b in probs])
avg = np.convolve(p, np.ones(5) / 5, mode="same")
user = ((t > 6.7) & (t < 7.5)) | ((t > 8.0) & (t < 8.8))
print(f"5-frame average: your stops peak {avg[user].max():.2f}; everything else peaks {avg[~user].max():.2f} at {t[~user][avg[~user].argmax()]:.2f}s")
for c in (0.20, 0.25, 0.30, 0.35, 0.40):
    print(f"  cutoff {c:.2f}: your stops {'CAUGHT' if (avg[user] >= c).any() else 'missed'}, false triggers elsewhere: {int((np.diff((avg[~user] >= c).astype(int)) == 1).sum())}")
