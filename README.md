# Faster by Train? 🚆🚗

**Pick your town. See who wins — train or car — to every municipality in Switzerland.**

One map, one question, one answer. No route planner, no settings — just the
car-vs-public-transport verdict from *your* home to all 2,069 Swiss municipalities
at a glance.

**Live map:** https://kaiman22.github.io/faster-by-train/

## How it works

Google Maps answers "how do I get from A to B?" This map answers a different
question: *"from where I live, where does the train actually beat the car?"* —
for the entire country at once. Red = train wins, blue = car wins, gray = toss-up.

## Data & method

All 2,069 × 2,069 pairs are **exact**, precomputed offline and served as static
per-origin binaries (12 KB each). No scraping, no API limits — everything comes
from open data:

- **Anchors**: each municipality is anchored at its **main public-transport stop**
  (busiest GTFS stop assigned by nearest-municipality rule), so PT is
  station-to-station and car is center-to-center. No phantom 20-minute walk legs.
- **Train/PT**: [MOTIS](https://github.com/motis-project/motis) routing on the
  official Swiss GTFS timetable (opentransportdata.swiss). Best journey across
  three departures (Mon 07:00/07:20/07:40; Sat 11:00/11:20/11:40 for the weekend
  layer) — commuters time their departure to the Taktfahrplan.
- **Car**: OSRM on the Swiss OpenStreetMap network, calibrated to Monday-commute
  traffic against 20,670 traffic-aware Google drive times (median residual ~5%;
  raw OSRM is ~34% too optimistic in urban areas).
- Deltas under 5 minutes are shown as toss-ups. Gray = no reasonable PT
  connection (over 8 h).

The full pipeline (`pipeline/`) reruns in ~30 minutes on a laptop when a new
timetable year is published.

## Stack

React + Vite + MapLibre GL, a single 0.8 MB static data file, no backend.

```bash
npm install && npm run dev
# rebuild data from the sleeper-towns repo:
python3 scripts/build_data.py
```
