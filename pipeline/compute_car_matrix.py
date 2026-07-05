#!/usr/bin/env python3
"""Compute the exact 2069x2069 car time matrix via a local OSRM server.

Prereq:
  osrm-extract -p <profiles>/car.lua switzerland.osm.pbf
  osrm-contract switzerland.osrm
  osrm-routed --algorithm ch --max-table-size 5000 switzerland.osrm

Output: car_matrix.npy (float32 seconds, N x N, row = origin)
"""
import json
import urllib.request
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[1]
DATA = json.load(open(BASE / "public" / "data.json"))
MUNIS = DATA["munis"]
N = len(MUNIS)
OSRM = "http://127.0.0.1:5000"

coords = ";".join(f"{m['lon']:.5f},{m['lat']:.5f}" for m in MUNIS)

# Chunk sources to keep responses manageable; all coords go in the URL once per request
CHUNK = 200
matrix = np.full((N, N), np.nan, dtype=np.float32)

for start in range(0, N, CHUNK):
    idx = list(range(start, min(start + CHUNK, N)))
    src = ";".join(str(i) for i in idx)
    url = f"{OSRM}/table/v1/driving/{coords}?sources={src}&annotations=duration"
    with urllib.request.urlopen(url, timeout=300) as r:
        res = json.load(r)
    if res.get("code") != "Ok":
        raise SystemExit(f"OSRM error at chunk {start}: {res.get('code')}")
    dur = res["durations"]
    for j, i in enumerate(idx):
        matrix[i, :] = dur[j]
    print(f"  rows {start}..{idx[-1]} done")

nan = int(np.isnan(matrix).sum())
print(f"matrix complete, NaN cells: {nan} ({100*nan/(N*N):.3f}%)")
np.save(BASE / "pipeline" / "car_matrix.npy", matrix)
print("saved pipeline/car_matrix.npy")
