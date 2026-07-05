#!/usr/bin/env python3
"""Calibrate free-flow OSRM car times to Monday-morning traffic reality.

OSRM has no traffic model. We have 20,670 traffic-aware Google drive times
(Monday commute, from the sleeper-towns scrape) for municipality->hub pairs.
Comparing both on identical pairs gives a duration-dependent correction:
OSRM is ~34% optimistic on short urban trips, ~2-5% pessimistic on long
highway runs. Applied as a smooth piecewise-linear factor over duration.

Input:  car_matrix.npy          (raw OSRM seconds)
Output: car_matrix_calibrated.npy
"""
import json
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[1]
PIPE = Path(__file__).resolve().parent

D = json.load(open(BASE / "public" / "data.json"))
MU = D["munis"]
car = np.load(PIPE / "car_matrix.npy")

hub_ix = {h: next(i for i, m in enumerate(MU) if m["id"] == mid)
          for h, mid in D["hubMuni"].items()}

# Median Google/OSRM ratio per 30-min OSRM band
bands = {}
for i, m in enumerate(MU):
    for h, gsec in m["car"].items():
        osec = car[i, hub_ix[h]]
        if osec > 0 and gsec > 0:
            bands.setdefault(int(osec // 1800), []).append(gsec / osec)

xs, ys = [], []
for b in sorted(bands):
    v = bands[b]
    if len(v) >= 30:  # only bands with solid support
        xs.append(b * 1800 + 900)
        ys.append(float(np.median(v)))
print("calibration anchors (min -> factor):",
      {int(x / 60): round(y, 3) for x, y in zip(xs, ys)})

factor = np.interp(car, xs, ys)  # flat extrapolation beyond anchor range
calibrated = car * factor
np.save(PIPE / "car_matrix_calibrated.npy", calibrated.astype(np.float32))

# Residual check on the known pairs
res = []
for i, m in enumerate(MU):
    for h, gsec in m["car"].items():
        c = calibrated[i, hub_ix[h]]
        if c > 0 and gsec > 0:
            res.append(abs(c - gsec) / gsec)
res = np.array(res)
print(f"residual vs Google after calibration: median {100*np.median(res):.1f}%, p80 {100*np.percentile(res,80):.1f}%")
print("saved car_matrix_calibrated.npy")
