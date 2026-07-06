#!/usr/bin/env python3
"""Re-anchor each municipality at its main public-transport stop.

Old anchors (settlement medoids) forced every PT journey through walk
access legs at both ends, systematically muting rail corridors. New anchor:
the busiest GTFS stop assigned to the municipality — station-to-station for
PT, center-to-center for car.

Assignment guard: every stop belongs to its NEAREST municipality anchor
(Voronoi), so a suburb can't adopt the neighboring city's Hauptbahnhof.
Municipalities keep their medoid if no assigned stop exists within 6 km.

Reads gtfs_ch.zip (stops.txt + stop_times.txt, streamed), updates
public/data.json in place (lat/lon + anchor stop name).
"""
import csv
import io
import json
import math
import zipfile
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
GTFS = Path(__file__).resolve().parent / "gtfs_ch.zip"

data = json.load(open(BASE / "public" / "data.json"))
MUNIS = data["munis"]
N = len(MUNIS)


def hav_km(lat1, lon1, lat2, lon2):
    R = 6371
    dla = math.radians(lat2 - lat1)
    dlo = math.radians(lon2 - lon1)
    a = math.sin(dla / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlo / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# --- muni grid for nearest-muni lookup ---
CELL = 0.02
grid = defaultdict(list)
for mi, m in enumerate(MUNIS):
    grid[(int(m["lat"] / CELL), int(m["lon"] / CELL))].append(mi)


def nearest_muni(lat, lon, max_km=6.0):
    ck, cl = int(lat / CELL), int(lon / CELL)
    best, bd = None, max_km
    reach = int(max_km / (CELL * 111)) + 2
    for dk in range(-reach, reach + 1):
        for dl in range(-reach, reach + 1):
            for mi in grid.get((ck + dk, cl + dl), ()):
                m = MUNIS[mi]
                d = hav_km(lat, lon, m["lat"], m["lon"])
                if d < bd:
                    best, bd = mi, d
    return best, bd


zf = zipfile.ZipFile(GTFS)

# --- stops: id -> (name, lat, lon, parent) ---
stops = {}
with zf.open("stops.txt") as f:
    for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
        try:
            stops[row["stop_id"]] = (
                row["stop_name"],
                float(row["stop_lat"]),
                float(row["stop_lon"]),
                row.get("parent_station") or "",
            )
        except (ValueError, KeyError):
            continue
print(f"stops: {len(stops)}")

# --- rail trip detection: route_type 2 / 100-117 (Swiss extended rail types) ---
rail_routes = set()
with zf.open("routes.txt") as f:
    for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
        try:
            rt = int(row["route_type"])
        except (ValueError, KeyError):
            continue
        if rt == 2 or 100 <= rt <= 117:
            rail_routes.add(row["route_id"])
print(f"rail routes: {len(rail_routes)}")

rail_trips = set()
with zf.open("trips.txt") as f:
    reader = csv.reader(io.TextIOWrapper(f, "utf-8-sig"))
    header = next(reader)
    ri, ti = header.index("route_id"), header.index("trip_id")
    for row in reader:
        if row[ri] in rail_routes:
            rail_trips.add(row[ti])
print(f"rail trips: {len(rail_trips)}")

# --- departure counts per stop (streamed, ~100M rows), split rail/total ---
counts = defaultdict(int)
rail_counts = defaultdict(int)
with zf.open("stop_times.txt") as f:
    reader = csv.reader(io.TextIOWrapper(f, "utf-8-sig"))
    header = next(reader)
    si = header.index("stop_id")
    tI = header.index("trip_id")
    for row in reader:
        sid = row[si]
        counts[sid] += 1
        if row[tI] in rail_trips:
            rail_counts[sid] += 1
print(f"stop_times counted: {sum(counts.values())} rows, {len(counts)} distinct stops")

# roll platform counts up to parent station (use parent's coords when present)
station_counts = defaultdict(int)
station_rail = defaultdict(int)
station_pos = {}
for sid, c in counts.items():
    if sid not in stops:
        continue
    name, lat, lon, parent = stops[sid]
    key = parent if parent and parent in stops else sid
    station_counts[key] += c
    station_rail[key] += rail_counts.get(sid, 0)
    if key not in station_pos:
        pname, plat, plon, _ = stops[key]
        station_pos[key] = (pname, plat, plon)

# --- assignment, three tiers per municipality ---
# 1. NAME-MATCHED rail station: Swiss train stations are named after their town
#    ("Lausanne", "Zürich HB"). Voronoi-on-medoids misassigns big-city stations
#    (Lausanne gare is nearer Pully's medoid than Lausanne's). Distance guard
#    <= 8 km disambiguates homonyms (four different "Buchs" exist).
# 2. Busiest rail station assigned by nearest-medoid (Voronoi).
# 3. Busiest stop of any kind (bus-only villages).
MIN_RAIL_DEP = 500  # filters freight sidings / museum lines

import unicodedata


def norm(s):
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()
    return s


muni_core = []
for m in MUNIS:
    core = m["n"].split(" (")[0]
    muni_core.append(norm(core))

best_named = {}
best_rail = {}
best_any = {}
for key, c in station_counts.items():
    name, lat, lon = station_pos[key]
    mi, d = nearest_muni(lat, lon)
    r = station_rail.get(key, 0)

    if r >= MIN_RAIL_DEP:
        first = norm(name.split(",")[0])
        # try name match against ALL munis within 8 km (grid lookup via nearest_muni
        # is not enough — the right muni may not be the nearest one)
        ck, cl = int(lat / CELL), int(lon / CELL)
        reach = int(8.0 / (CELL * 111)) + 2
        for dk in range(-reach, reach + 1):
            for dl in range(-reach, reach + 1):
                for mj in grid.get((ck + dk, cl + dl), ()):
                    if not first.startswith(muni_core[mj]):
                        continue
                    if hav_km(lat, lon, MUNIS[mj]["lat"], MUNIS[mj]["lon"]) > 8.0:
                        continue
                    cur = best_named.get(mj)
                    # prefer longer (more specific) name match, then rail count
                    score = (len(muni_core[mj]), r)
                    if cur is None or score > cur[0]:
                        best_named[mj] = (score, name, lat, lon)

    if mi is None:
        continue
    if r >= MIN_RAIL_DEP:
        cur = best_rail.get(mi)
        if cur is None or r > cur[0]:
            best_rail[mi] = (r, name, lat, lon)
    cur = best_any.get(mi)
    if cur is None or c > cur[0]:
        best_any[mi] = (c, name, lat, lon)

moved = kept = named_n = rail_n = 0
for mi, m in enumerate(MUNIS):
    hit = best_named.get(mi) or best_rail.get(mi) or best_any.get(mi)
    if hit:
        if mi in best_named:
            named_n += 1
        elif mi in best_rail:
            rail_n += 1
        _, name, lat, lon = hit
        m["lat"], m["lon"] = round(lat, 5), round(lon, 5)
        m["anchor"] = name
        moved += 1
    else:
        m["anchor"] = None
        kept += 1
print(f"name-matched rail: {named_n}, voronoi rail: {rail_n}")

print(f"anchored at main stop: {moved}, kept medoid: {kept}")
json.dump(data, open(BASE / "public" / "data.json", "w"), ensure_ascii=False, separators=(",", ":"))

for probe in ("Zürich", "Bern", "Küssnacht (SZ)", "Poschiavo"):
    m = next(x for x in MUNIS if x["n"] == probe)
    print(f"  {probe}: anchor = {m['anchor']}")
