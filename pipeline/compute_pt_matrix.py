#!/usr/bin/env python3
"""Compute the exact NxN public-transport matrix via local MOTIS one-to-all.

For each origin municipality centroid: one-to-all returns door-to-stop
durations (minutes, incl. initial walk via OSM street routing) for every
reachable stop. Per target municipality we take
    min over stops near the target [ duration(stop) + walk(stop -> centroid) ]
with walk at 13 min/km, candidate stops within 1.5 km (else nearest <= 6 km).

Usage:
  python3 compute_pt_matrix.py 2026-07-06T05:00:00Z pt_weekday.npy   # Mon 07:00 CEST
  python3 compute_pt_matrix.py 2026-07-11T09:00:00Z pt_weekend.npy   # Sat 11:00 CEST

Output: N x N float32 minutes, row = origin. NaN where unreachable.
"""
import json
import math
import sys
import time as _time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[1]
DATA = json.load(open(BASE / "public" / "data.json"))
MUNIS = DATA["munis"]
N = len(MUNIS)
MOTIS = "http://127.0.0.1:8080"
MAX_MIN = 480
WALK_MIN_PER_KM = 13.0
NEAR_KM = 1.5
FAR_KM = 6.0
WORKERS = 4

# Comma-separated departure times; per pair we take the fastest journey across
# them. Models a commuter who times their departure to the timetable instead
# of walking out the door at exactly 07:00 (otherwise up-front wait unfairly
# penalizes low-frequency lines vs the car).
DEP_TIMES = (sys.argv[1] if len(sys.argv) > 1 else "2026-07-06T05:00:00Z").split(",")
OUT = sys.argv[2] if len(sys.argv) > 2 else "pt_weekday.npy"


def hav_km(lat1, lon1, lat2, lon2):
    R = 6371
    dla = math.radians(lat2 - lat1)
    dlo = math.radians(lon2 - lon1)
    a = math.sin(dla / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlo / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# stop registry: stopId -> index; per-muni candidates: [(stop_index, walk_min)]
stop_id_to_ix = {}
stop_coords = []          # parallel list of (lat, lon)
muni_candidates = [[] for _ in range(N)]
muni_nearest = [None] * N  # fallback: (stop_index, walk_min) nearest within FAR_KM

# small grid over munis for fast stop->muni assignment
CELL = 0.02  # ~1.6-2.2 km
grid = {}
for mi, m in enumerate(MUNIS):
    key = (int(m["lat"] / CELL), int(m["lon"] / CELL))
    grid.setdefault(key, []).append(mi)


import threading
_reg_lock = threading.Lock()


def register_stop(sid, lat, lon):
    six = stop_id_to_ix.get(sid)
    if six is not None:
        return six
    return _register_stop_locked(sid, lat, lon)


def _register_stop_locked(sid, lat, lon):
    with _reg_lock:
        six = stop_id_to_ix.get(sid)
        if six is not None:
            return six
        return _do_register(sid, lat, lon)


def _do_register(sid, lat, lon):
    six = len(stop_coords)
    stop_id_to_ix[sid] = six
    stop_coords.append((lat, lon))
    # attach to nearby munis
    ck, cl = int(lat / CELL), int(lon / CELL)
    reach = int(FAR_KM / (CELL * 111)) + 2
    for dk in range(-reach, reach + 1):
        for dl in range(-reach, reach + 1):
            for mi in grid.get((ck + dk, cl + dl), ()):
                m = MUNIS[mi]
                d = hav_km(lat, lon, m["lat"], m["lon"])
                if d <= NEAR_KM:
                    muni_candidates[mi].append((six, d * WALK_MIN_PER_KM))
                elif d <= FAR_KM:
                    cur = muni_nearest[mi]
                    if cur is None or d * WALK_MIN_PER_KM < cur[1]:
                        muni_nearest[mi] = (six, d * WALK_MIN_PER_KM)
    return six


def one_to_all(lat, lon, dep_time, retries=3):
    params = {"one": f"{lat},{lon}", "time": dep_time, "maxTravelTime": MAX_MIN}
    url = f"{MOTIS}/api/v1/one-to-all?{urllib.parse.urlencode(params)}"
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                return json.load(r)
        except Exception:
            if attempt == retries:
                raise
            _time.sleep(2 * (attempt + 1))


def row_for(oi):
    o = MUNIS[oi]
    dur = {}
    for dep in DEP_TIMES:
        res = one_to_all(o["lat"], o["lon"], dep)
        for item in res["all"]:
            p = item["place"]
            sid = p.get("stopId")
            if not sid:
                continue
            six = register_stop(sid, p["lat"], p["lon"])
            d = item["duration"]
            if six not in dur or d < dur[six]:
                dur[six] = d

    row = np.full(N, np.nan, dtype=np.float32)
    for ti in range(N):
        if ti == oi:
            row[ti] = 0
            continue
        best = None
        for six, walk in muni_candidates[ti]:
            d = dur.get(six)
            if d is not None:
                t = d + walk
                if best is None or t < best:
                    best = t
        if best is None and muni_nearest[ti]:
            six, walk = muni_nearest[ti]
            d = dur.get(six)
            if d is not None:
                best = d + walk
        if best is not None and best <= MAX_MIN:
            row[ti] = best
    return oi, row


def main():
    matrix = np.full((N, N), np.nan, dtype=np.float32)
    t0 = _time.time()
    done = 0
    # register_stop mutates shared structures; GIL makes appends safe enough,
    # but run the first row solo so the bulk of stops registers once
    oi, row = row_for(0)
    matrix[oi] = row
    done = 1
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for oi, row in ex.map(row_for, range(1, N)):
            matrix[oi] = row
            done += 1
            if done % 100 == 0:
                el = _time.time() - t0
                print(f"{done}/{N} rows, {el:.0f}s elapsed, ETA {el/done*(N-done):.0f}s", flush=True)

    ok = ~np.isnan(matrix)
    print(f"coverage: {100*ok.sum()/(N*N):.2f}% cells, median {np.nanmedian(matrix):.0f} min")
    np.save(BASE / "pipeline" / OUT, matrix)
    print(f"saved pipeline/{OUT}")


if __name__ == "__main__":
    main()
