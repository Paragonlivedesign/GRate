"""Verify live EQ tweaks no longer click: max discontinuity stays tiny."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np
from grate.dsp.bands import ChannelProcessor

SR = 48000
BLOCK = 1024

def run(adjust):
    p = ChannelProcessor(sample_rate=SR)
    t = np.arange(SR * 2) / SR
    sig = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    out = []
    for i in range(0, sig.size, BLOCK):
        blk_idx = i // BLOCK
        adjust(p, blk_idx)
        out.append(p.process(sig[i : i + BLOCK]))
    y = np.concatenate(out)
    # worst sample-to-sample jump after warmup
    d = np.abs(np.diff(y[SR // 4 :]))
    return float(d.max()), p

# Control: no changes at all
ctl, _ = run(lambda p, b: None)

# Torture: every block, alternate big shelf boosts + HP sweeps (like dragging fast)
def torture(p, b):
    if b < 10:
        return
    ls = 12.0 if b % 2 else -12.0
    p.configure_eq(ls, -ls, False, -50.0, hp_q=3.0 if b % 2 else 0.707,
                   low_shelf_hz=200.0, high_shelf_hz=4000.0)
    p.set_freq_range(40.0 + (b % 20) * 30.0, 18000.0)

tor, p = run(torture)

# Same torture but simulating the OLD behavior: params jump instantly
def torture_instant(p, b):
    torture(p, b)
    if p._dirty:
        p._cur = p._param_targets()
        p._dirty = False
        p._redesign()

old, _ = run(torture_instant)

# Let it settle and confirm the glide converges
for _ in range(200):
    p.process(np.zeros(BLOCK, dtype=np.float32))
converged = not p._dirty

print(f"control (no EQ changes) max diff : {ctl:.5f}")
print(f"smoothed glide          max diff : {tor:.5f}")
print(f"old instant jump        max diff : {old:.5f}")
print(f"glide converged  : {converged}")
assert tor < old * 0.6, "smoothing did not reduce discontinuities"
assert converged
print("OK")
