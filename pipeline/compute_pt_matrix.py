#!/usr/bin/env python3
"""Compute the exact 2069x2069 public-transport time matrix via local MOTIS.

For each origin municipality centroid, runs a MOTIS one-to-all query
(door-to-door, includes first/last-mile walk via OSM street routing) and
records the fastest arrival at every other municipality centroid.

Prereq: ./motis server -d data  (after ./motis import with GTFS + OSM)

Usage:
  python3 compute_pt_matrix.py 2026-07-06T07:00 pt_weekday.npy   # Monday
  python3 compute_pt_matrix.py 2026-07-11T11:00 pt_weekend.npy   # Saturday

Output: N x N float32 seconds, row = origin. NaN where unreachable.
"""
import json
import sys
import time as _time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[1]
DATA = json.load(open(BASE / "public" / "data.json"))
MUNIS = DATA["munis"]
N = len(MUNIS)
MOTIS = "http://127.0.0.1:8080"
MAX_TRAVEL_MIN = 480  # cap searches at 8h — covers every realistic Swiss pair

DEP_TIME = sys.argv[1] if len(sys.argv) > 1 else "2026-07-06T07:00"
OUT = sys.argv[2] if len(sys.argv) > 2 else "pt_weekday.npy"


def one_to_all(lat, lon):
    """All reachable places from (lat, lon). Returns dict place->seconds."""
    params = {
        "one": f"{lat},{lon}",
        "time": DEP_TIME + ":00+02:00",
        "maxTravelTime": MAX_TRAVEL_MIN,
        "arriveBy": "false",
    }
    url = f"{MOTIS}/api/v1/one-to-all?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=600) as r:
        return json.load(r)


def main():
    matrix = np.full((N, N), np.nan, dtype=np.float32)
    t0 = _time.time()
    for i, o in enumerate(MUNIS):
        try:
            res = one_to_all(o["lat"], o["lon"])
        except Exception as e:
            print(f"  row {i} ({o['n']}): FAILED {e}")
            continue
        #

        # Response shape is resolved at runtime in probe_api.py; the reachable
        # list maps stop/place coordinates to duration. We match targets by
        # nearest reachable point within walking range of each centroid.
        raise SystemExit(
            "Template: adapt parsing to probed one-to-all response shape "
            "(see probe_api.py output) before running the full matrix."
        )
    np.save(BASE / "pipeline" / OUT, matrix)


if __name__ == "__main__":
    main()
