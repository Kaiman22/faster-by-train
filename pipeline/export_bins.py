#!/usr/bin/env python3
"""Export per-origin binary matrix files for the static site.

Input:  car_matrix.npy, pt_weekday.npy [, pt_weekend.npy]  (N x N float32 seconds)
Output: public/matrix/{muniId}.bin — little-endian Uint16 minutes:
          [0..N-1]   car
          [N..2N-1]  pt weekday
          [2N..3N-1] pt weekend (only if available)
        65535 = unreachable/unknown. Order matches data.json munis array.

Also stamps data.json with matrix metadata (mode count, generated date).
"""
import json
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[1]
PIPE = BASE / "pipeline"
OUTDIR = BASE / "public" / "matrix"
OUTDIR.mkdir(exist_ok=True)

data = json.load(open(BASE / "public" / "data.json"))
munis = data["munis"]
N = len(munis)

layers = []
for name, fname in (("car", "car_matrix_calibrated.npy"),
                    ("pt_weekday", "pt_weekday.npy"),
                    ("pt_weekend", "pt_weekend.npy")):
    p = PIPE / fname
    if p.exists():
        m = np.load(p)
        assert m.shape == (N, N), f"{fname}: shape {m.shape} != ({N},{N})"
        layers.append((name, m))
        print(f"loaded {fname} as layer '{name}'")

assert len(layers) >= 2, "need at least car + weekday PT"

UNREACH = 65535
stacked = []
for name, m in layers:
    # units differ by source: OSRM car matrix is seconds, MOTIS PT is minutes
    vals = m / 60.0 if name == "car" else m
    minutes = np.where(np.isnan(vals), UNREACH, np.round(vals))
    minutes = np.clip(minutes, 0, UNREACH).astype(np.uint16)
    stacked.append(minutes)

for i, mu in enumerate(munis):
    buf = b"".join(layer[i, :].tobytes() for layer in stacked)
    (OUTDIR / f"{mu['id']}.bin").write_bytes(buf)

data["matrix"] = {"layers": [n for n, _ in layers], "n": N}
json.dump(data, open(BASE / "public" / "data.json", "w"), ensure_ascii=False, separators=(",", ":"))

total = sum(f.stat().st_size for f in OUTDIR.glob("*.bin"))
print(f"wrote {N} files, {total/1e6:.1f} MB total, {total/N/1024:.1f} KB per origin")
