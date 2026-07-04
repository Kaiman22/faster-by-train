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

Built on the [Sleeper Towns](https://github.com/Kaiman22/sleeper-towns) dataset:
real routing data for 3,966 Swiss settlement points — car times (Google/Geoapify)
and public-transport times (SBB, Monday 07:00 commute snapshot) to 10 hub cities,
aggregated to municipality level.

Since an all-pairs matrix (2,069² ≈ 4.3M pairs) can't be scraped, times between
arbitrary municipalities are estimated client-side:

- **Car**: exact where the target is a hub city; otherwise a piecewise speed model
  fitted on 39,580 real (distance → drive time) pairs. Median error ~11%.
- **Train/PT**: exact where the target is a hub city; otherwise hub triangulation —
  `min over hubs [ PT(origin→hub) + transfer + PT(target→hub) ]` with
  Taktfahrplan transfer times, capped by a car-ratio plausibility bound.
  Median error ~11%.
- **Winner-sign accuracy** (the one thing the map shows): **~91%** on held-out
  ground truth. Deltas under 5 minutes are shown as toss-ups, which absorbs most
  of the remaining noise.

## Roadmap

- **Weekend mode**: weekday vs. weekend PT comparison (needs a Saturday scrape of
  the settlement→hub matrix — script ready in the sleeper-towns repo).
- **Exact car matrix**: local OSRM (Docker) can compute all 2,069² car times
  exactly in seconds; would replace the distance model.

## Stack

React + Vite + MapLibre GL, a single 0.8 MB static data file, no backend.

```bash
npm install && npm run dev
# rebuild data from the sleeper-towns repo:
python3 scripts/build_data.py
```
