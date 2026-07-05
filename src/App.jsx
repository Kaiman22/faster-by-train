import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import maplibregl from 'maplibre-gl'

const DATA_URL = './data.json'
const BASEMAP = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json'

// Neutral band: |car - pt| below this is a toss-up (also absorbs estimation noise)
const NEUTRAL_S = 300
// Color saturates at this delta
const MAX_S = 1800

const CAR_COLOR = '#1a6ee0'   // car wins
const TRAIN_COLOR = '#eb0000' // train wins (SBB red)
const NEUTRAL_COLOR = '#d8d5d0'

// BFS canton number -> abbreviation
const CANTONS = {
  '01': 'ZH', '02': 'BE', '03': 'LU', '04': 'UR', '05': 'SZ', '06': 'OW',
  '07': 'NW', '08': 'GL', '09': 'ZG', '10': 'FR', '11': 'SO', '12': 'BS',
  '13': 'BL', '14': 'SH', '15': 'AR', '16': 'AI', '17': 'SG', '18': 'GR',
  '19': 'AG', '20': 'TG', '21': 'TI', '22': 'VD', '23': 'VS', '24': 'NE',
  '25': 'GE', '26': 'JU',
}

function normalize(s) {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

function fmtMin(sec) {
  const m = Math.round(sec / 60)
  if (m < 60) return `${m} min`
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}`
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371
  const dLat = ((lat2 - lat1) * Math.PI) / 180
  const dLon = ((lon2 - lon1) * Math.PI) / 180
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLon / 2) ** 2
  return R * 2 * Math.asin(Math.sqrt(a))
}

// Piecewise car time estimate from fitted sec/km bands
function makeCarEstimator(model) {
  const bands = Object.entries(model).map(([k, spk]) => {
    const [lo, hi] = k.split('-').map(Number)
    return { lo, hi, spk }
  })
  const last = bands[bands.length - 1]
  return (distKm) => {
    for (const b of bands) if (distKm >= b.lo && distKm < b.hi) return distKm * b.spk
    return distKm * last.spk
  }
}

/**
 * Car + PT estimates between two municipalities.
 * Car: exact where the target is a hub city (we have real drive times),
 * otherwise the calibrated distance model (~11% median error).
 * PT: exact where target is a hub city, otherwise hub triangulation
 * min over hubs [ pt(O→h) + transfer(h) + pt(T→h) ], capped by a
 * car-ratio bound. Winner-sign accuracy vs ground truth: ~91%.
 */
function estimatePair(o, t, hubs, hubOfMuni, carEst) {
  const dist = haversineKm(o.lat, o.lon, t.lat, t.lon)
  const tHub = hubOfMuni[t.id]
  const oHub = hubOfMuni[o.id]

  let carSec = null
  if (tHub && o.car[tHub] != null) carSec = o.car[tHub]
  else if (oHub && t.car[oHub] != null) carSec = t.car[oHub]
  else carSec = carEst(dist)

  let ptSec = null
  if (tHub && o.pt[tHub] != null) {
    ptSec = o.pt[tHub]
  } else if (oHub && t.pt[oHub] != null) {
    ptSec = t.pt[oHub]
  } else {
    let best = null
    for (const h in hubs) {
      const leg1 = o.pt[h]
      const leg2 = t.pt[h]
      if (leg1 == null || leg2 == null) continue
      const total = leg1 + hubs[h].transfer + leg2
      if (best == null || total < best) best = total
    }
    ptSec = best
  }

  // Plausibility cap: PT rarely exceeds car by more than the typical ratio
  if (ptSec != null && carSec != null) {
    const ratio = carSec < 1800 ? 1.3 : carSec < 4800 ? 1.5 : 1.8
    ptSec = Math.min(ptSec, Math.round(carSec * ratio))
  }

  return { carSec, ptSec, dist }
}

export default function App() {
  const [data, setData] = useState(null)
  const [originId, setOriginId] = useState(null)
  const [query, setQuery] = useState('')
  const [hovered, setHovered] = useState(null)
  // Exact per-origin matrix (Uint16 minutes; layers: car, pt_weekday[, pt_weekend])
  const [matrix, setMatrix] = useState(null)
  const [ptLayer, setPtLayer] = useState('weekday')
  const matrixCache = useRef(new Map())
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const mapReady = useRef(false)

  useEffect(() => {
    fetch(DATA_URL)
      .then((r) => r.json())
      .then((d) => {
        setData(d)
        const params = new URLSearchParams(window.location.search)
        const from = params.get('from')
        if (from && d.munis.some((m) => m.id === from)) setOriginId(from)
      })
  }, [])

  const muniById = useMemo(() => {
    if (!data) return {}
    const idx = {}
    for (const m of data.munis) idx[m.id] = m
    return idx
  }, [data])

  const hubOfMuni = useMemo(() => {
    if (!data) return {}
    const inv = {}
    for (const h in data.hubMuni) inv[data.hubMuni[h]] = h
    return inv
  }, [data])

  const carEst = useMemo(() => (data ? makeCarEstimator(data.carModel) : null), [data])

  const origin = originId ? muniById[originId] : null

  // Fetch the exact per-origin matrix file (if published); fall back to
  // client-side estimation when unavailable.
  useEffect(() => {
    setMatrix(null)
    if (!originId || !data?.matrix) return
    if (matrixCache.current.has(originId)) {
      setMatrix(matrixCache.current.get(originId))
      return
    }
    let cancelled = false
    fetch(`./matrix/${originId}.bin`)
      .then((r) => (r.ok ? r.arrayBuffer() : null))
      .then((buf) => {
        if (cancelled || !buf) return
        const u16 = new Uint16Array(buf)
        matrixCache.current.set(originId, u16)
        setMatrix(u16)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [originId, data])

  const hasWeekend = data?.matrix?.layers?.includes('pt_weekend')
  const exact = matrix != null

  // Compute deltas for all municipalities from the selected origin
  const results = useMemo(() => {
    if (!data || !origin) return null
    const out = {}

    if (matrix && data.matrix) {
      const N = data.matrix.n
      const layers = data.matrix.layers
      const ptIdx = ptLayer === 'weekend' && layers.includes('pt_weekend')
        ? layers.indexOf('pt_weekend')
        : layers.indexOf('pt_weekday')
      const carIdx = layers.indexOf('car')
      data.munis.forEach((t, ti) => {
        if (t.id === origin.id) return
        const carMin = matrix[carIdx * N + ti]
        const ptMin = matrix[ptIdx * N + ti]
        if (carMin === 65535 || ptMin === 65535) return
        const carSec = carMin * 60
        const ptSec = ptMin * 60
        out[t.id] = { carSec, ptSec, delta: carSec - ptSec }
      })
      return out
    }

    if (!carEst) return null
    for (const t of data.munis) {
      if (t.id === origin.id) continue
      const { carSec, ptSec } = estimatePair(origin, t, data.hubs, hubOfMuni, carEst)
      if (carSec == null || ptSec == null) continue
      out[t.id] = { carSec, ptSec, delta: carSec - ptSec }
    }
    return out
  }, [data, origin, hubOfMuni, carEst, matrix, ptLayer])

  // Init map
  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASEMAP,
      center: [8.2275, 46.8182],
      zoom: 7.2,
      minZoom: 6,
      maxZoom: 12,
      attributionControl: false,
    })
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    mapRef.current = map
    map.on('load', () => {
      mapReady.current = true
      map.resize() // container may have been sized after map construction
      setData((d) => (d ? { ...d } : d)) // retrigger layer effect once style is ready
    })
    requestAnimationFrame(() => map.resize())
    return () => map.remove()
  }, [])

  // Build/update the municipality layer
  useEffect(() => {
    const map = mapRef.current
    if (!map || !data || !mapReady.current) return

    const features = data.munis.map((m) => {
      const r = results?.[m.id]
      return {
        type: 'Feature',
        properties: {
          id: m.id,
          name: m.n,
          kt: m.kt,
          delta: r ? r.delta : null,
          car: r ? r.carSec : null,
          pt: r ? r.ptSec : null,
          isOrigin: origin && m.id === origin.id,
        },
        geometry: { type: 'Point', coordinates: [m.lon, m.lat] },
      }
    })
    const fc = { type: 'FeatureCollection', features }

    if (map.getSource('munis')) {
      map.getSource('munis').setData(fc)
      return
    }

    map.addSource('munis', { type: 'geojson', data: fc })
    map.addLayer({
      id: 'munis-circles',
      type: 'circle',
      source: 'munis',
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 3, 8, 6, 10, 10, 12, 16],
        'circle-color': [
          'case',
          ['==', ['get', 'delta'], null],
          '#c9c6c0',
          [
            'interpolate', ['linear'], ['get', 'delta'],
            -MAX_S, CAR_COLOR,
            -NEUTRAL_S, NEUTRAL_COLOR,
            NEUTRAL_S, NEUTRAL_COLOR,
            MAX_S, TRAIN_COLOR,
          ],
        ],
        'circle-opacity': ['case', ['==', ['get', 'delta'], null], 0.35, 0.85],
        'circle-stroke-width': 0.5,
        'circle-stroke-color': 'rgba(255,255,255,0.8)',
      },
    })
    map.addLayer({
      id: 'munis-origin',
      type: 'circle',
      source: 'munis',
      filter: ['==', ['get', 'isOrigin'], true],
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 6, 10, 14],
        'circle-color': '#111',
        'circle-stroke-width': 3,
        'circle-stroke-color': '#fff',
      },
    })

    map.on('mousemove', 'munis-circles', (e) => {
      map.getCanvas().style.cursor = 'pointer'
      if (e.features?.length) setHovered({ p: e.features[0].properties, x: e.point.x, y: e.point.y })
    })
    map.on('mouseleave', 'munis-circles', () => {
      map.getCanvas().style.cursor = ''
      setHovered(null)
    })
    map.on('click', 'munis-circles', (e) => {
      if (e.features?.length) selectOrigin(e.features[0].properties.id)
    })
  }, [data, results, origin])

  const selectOrigin = useCallback((id) => {
    setOriginId(id)
    setQuery('')
    setHovered(null)
    const url = new URL(window.location)
    url.searchParams.set('from', id)
    window.history.replaceState(null, '', url)
  }, [])

  const searchResults = useMemo(() => {
    if (!data || query.length < 2) return []
    const q = normalize(query)
    const starts = []
    const contains = []
    for (const m of data.munis) {
      const n = normalize(m.n)
      if (n.startsWith(q)) starts.push(m)
      else if (n.includes(q)) contains.push(m)
      if (starts.length >= 8) break
    }
    return [...starts, ...contains].slice(0, 8)
  }, [data, query])

  const stats = useMemo(() => {
    if (!results) return null
    let train = 0, car = 0, tie = 0
    for (const id in results) {
      const d = results[id].delta
      if (d > NEUTRAL_S) train++
      else if (d < -NEUTRAL_S) car++
      else tie++
    }
    return { train, car, tie }
  }, [results])

  const searchBox = (
    <div className="searchbox">
      <input
        autoFocus={!origin}
        type="text"
        placeholder="Type your municipality…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {searchResults.length > 0 && (
        <div className="search-results">
          {searchResults.map((m) => (
            <div key={m.id} className="search-result" onClick={() => selectOrigin(m.id)}>
              {m.n} <span className="kt">{CANTONS[m.kt] || m.kt}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )

  return (
    <div className="app">
      <div ref={containerRef} className="map" />

      {/* Intro card (no origin yet) */}
      {data && !origin && (
        <div className="intro-wrap">
          <div className="card intro-card">
            <h1>🚆 Faster by Train? 🚗</h1>
            <p className="tagline">
              Pick your town. See who wins — train or car — to every municipality in Switzerland.
            </p>
            {searchBox}
            <p className="hint">…or click your municipality on the map</p>
          </div>
        </div>
      )}

      {/* Compact header (origin picked) */}
      {origin && (
        <div className="card header-card">
          <div className="header-title">🚆 Faster by Train? 🚗</div>
          <div className="header-from">
            from <strong>{origin.n}</strong>
            <button className="change-btn" onClick={() => { setOriginId(null); const u = new URL(window.location); u.searchParams.delete('from'); window.history.replaceState(null, '', u) }}>
              change
            </button>
          </div>
          {stats && (
            <div className="header-stats">
              <span style={{ color: TRAIN_COLOR }}>🚆 {stats.train}</span>
              <span style={{ color: '#888' }}>🤝 {stats.tie}</span>
              <span style={{ color: CAR_COLOR }}>🚗 {stats.car}</span>
            </div>
          )}
          {hasWeekend && exact && (
            <div className="day-toggle">
              <button
                className={ptLayer === 'weekday' ? 'active' : ''}
                onClick={() => setPtLayer('weekday')}
              >
                Weekday
              </button>
              <button
                className={ptLayer === 'weekend' ? 'active' : ''}
                onClick={() => setPtLayer('weekend')}
              >
                Weekend
              </button>
            </div>
          )}
        </div>
      )}

      {/* Legend */}
      {origin && (
        <div className="card legend">
          <span className="legend-label" style={{ color: CAR_COLOR }}>🚗 Car wins</span>
          <div className="legend-bar" />
          <span className="legend-label" style={{ color: TRAIN_COLOR }}>Train wins 🚆</span>
        </div>
      )}

      {/* Tooltip */}
      {hovered && hovered.p.delta != null && (
        <div className="tooltip" style={{ left: hovered.x + 14, top: hovered.y - 10 }}>
          <div className="tooltip-name">{hovered.p.name}</div>
          <div className="tooltip-verdict">
            {hovered.p.delta > NEUTRAL_S
              ? `🚆 Train wins by ${fmtMin(hovered.p.delta)}`
              : hovered.p.delta < -NEUTRAL_S
              ? `🚗 Car wins by ${fmtMin(-hovered.p.delta)}`
              : '🤝 Toss-up'}
          </div>
          <div className="tooltip-times">
            🚗 {fmtMin(hovered.p.car)} · 🚆 {fmtMin(hovered.p.pt)}
          </div>
        </div>
      )}

      <div className="footer">
        {exact
          ? 'Exact door-to-door times · Swiss GTFS timetable + OSM road routing · not a route planner'
          : 'Estimates from real Swiss routing data (Mon 07:00 commute) · ~90% winner accuracy · not a route planner'}{' '}
        · <a href="https://github.com/Kaiman22/faster-by-train" target="_blank" rel="noreferrer">GitHub</a>
      </div>
    </div>
  )
}
