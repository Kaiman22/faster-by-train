#!/usr/bin/env python3
"""Build the compact static dataset for Faster by Train?

Reads the scored settlement GeoJSON from the sibling autonomy-explorer
(sleeper-towns) project and aggregates it to municipality level:
per municipality, the best (min) car and PT time to each of the 10 hub
cities across its settlements, plus mean coordinates.

Also fits the piecewise car speed model (sec/km per distance band) on all
known (distance, drive_time) pairs; the frontend uses it to estimate car
times between arbitrary municipalities. Validated: ~11% median abs error,
91% accuracy on the car-vs-train winner sign.

Output: public/data.json (~1.5 MB raw)
"""
import json
import math
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "autonomy-explorer" / "frontend" / "public" / "data" / "municipalities_scored.geojson"
OUT = Path(__file__).resolve().parents[1] / "public" / "data.json"

# Hub station coords + Taktfahrplan transfer seconds (same as sleeper-towns)
HUBS = {
    "zurich":     {"lat": 47.378, "lon": 8.540, "transfer": 450},
    "bern":       {"lat": 46.949, "lon": 7.439, "transfer": 450},
    "basel":      {"lat": 47.548, "lon": 7.589, "transfer": 450},
    "luzern":     {"lat": 47.050, "lon": 8.310, "transfer": 600},
    "geneve":     {"lat": 46.210, "lon": 6.143, "transfer": 750},
    "lausanne":   {"lat": 46.517, "lon": 6.629, "transfer": 600},
    "stgallen":   {"lat": 47.423, "lon": 9.370, "transfer": 600},
    "lugano":     {"lat": 46.005, "lon": 8.947, "transfer": 750},
    "winterthur": {"lat": 47.500, "lon": 8.724, "transfer": 600},
    "biel":       {"lat": 47.133, "lon": 7.243, "transfer": 750},
}
BANDS = [(0, 10), (10, 25), (25, 50), (50, 100), (100, 200), (200, 400)]


def hav(a, b, c, d):
    R = 6371
    dla = math.radians(c - a)
    dlo = math.radians(d - b)
    x = math.sin(dla / 2) ** 2 + math.cos(math.radians(a)) * math.cos(math.radians(c)) * math.sin(dlo / 2) ** 2
    return R * 2 * math.asin(math.sqrt(x))


def main():
    gj = json.load(open(SRC))
    cities = list(gj["metadata"]["cities"].keys())

    munis = {}
    car_pairs = []
    for f in gj["features"]:
        p = f["properties"]
        d = p["drive_times"]
        pt = p["pt_times"]
        if isinstance(d, str):
            d = json.loads(d)
        if isinstance(pt, str):
            pt = json.loads(pt)
        lon, lat = f["geometry"]["coordinates"]

        for c in cities:
            if d.get(c):
                car_pairs.append((hav(lat, lon, HUBS[c]["lat"], HUBS[c]["lon"]), d[c]))

        mid = p["municipality_id"] or p["id"]
        m = munis.setdefault(mid, {
            "n": p["name"], "kt": p["canton_code"],
            "lats": [], "lons": [], "car": {}, "pt": {},
        })
        m["lats"].append(lat)
        m["lons"].append(lon)
        for c in cities:
            if d.get(c) and (c not in m["car"] or d[c] < m["car"][c]):
                m["car"][c] = d[c]
            if pt.get(c) and (c not in m["pt"] or pt[c] < m["pt"][c]):
                m["pt"][c] = pt[c]

    # Fit car model: median sec/km per distance band
    model = {}
    for lo, hi in BANDS:
        xs = sorted(s / max(dist, 0.5) for dist, s in car_pairs if lo <= dist < hi)
        if xs:
            model[f"{lo}-{hi}"] = round(xs[len(xs) // 2], 2)

    out_munis = []
    for mid, m in munis.items():
        if not m["car"] and not m["pt"]:
            continue
        out_munis.append({
            "id": mid,
            "n": m["n"],
            "kt": m["kt"],
            "lat": round(sum(m["lats"]) / len(m["lats"]), 5),
            "lon": round(sum(m["lons"]) / len(m["lons"]), 5),
            "car": m["car"],
            "pt": m["pt"],
        })

    # Which municipality contains each hub (for exact-PT special case)
    hub_muni = {}
    for h, hc in HUBS.items():
        best = min(out_munis, key=lambda m: hav(m["lat"], m["lon"], hc["lat"], hc["lon"]))
        hub_muni[h] = best["id"]

    data = {
        "hubs": HUBS,
        "hubMuni": hub_muni,
        "carModel": model,
        "munis": sorted(out_munis, key=lambda m: m["n"]),
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(out_munis)} municipalities -> {OUT} ({OUT.stat().st_size/1e6:.2f} MB)")
    print("car model:", model)


if __name__ == "__main__":
    main()
