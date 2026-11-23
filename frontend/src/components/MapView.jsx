import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../store.js'
import { tierFills } from '../lib/color.js'
import { reasonList } from '../lib/reasons.js'

const MIN_K = 1
const MAX_K = 10
const DRAG_PX = 4 // movement beyond this turns a press into a pan, not a click
const MUMBAI = new Set([1, 2])
const MUMBAI_DETAIL_K = 3 // above this zoom, Mumbai City and Suburban get their own labels
const FS = 8.5 // label size in map units at zoom 1 (labels grow a little when zoomed, never huge)
// Recolor timing; keep in sync with .district in styles.css. A profile, chat or reset change plays
// a wave from #1 down the ranking; a slider drag recolors at once so the map keeps up with the pointer.
export const FILL_TRANSITION_MS = 520
export const SLIDER_FILL_MS = 220
const WAVE_STEP_MS = 14
const INTRO_SWEEP_MS = 600 // first paint: districts fade in from west to east
const FRAME_MS = 17

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v))

// Short labels that fit inside the district shapes (from the old project).
const SHORT_NAMES = {
  'Mumbai City': 'MUM', 'Mumbai Suburban': 'M.Sub', Ratnagiri: 'Ratn.', Sindhudurg: 'Sindh.', Palghar: 'Palgh.',
  Ahmednagar: 'Ahmd.', Nandurbar: 'Nand.', Jalgaon: 'Jalg.', Solapur: 'Solap.', Kolhapur: 'Kolh.',
  Aurangabad: 'Aurang.', Osmanabad: 'Osm.', Hingoli: 'Hing.', Parbhani: 'Parb.', Yavatmal: 'Yavat.',
  Amravati: 'Amrav.', Washim: 'Wash.', Buldhana: 'Buld.', Chandrapur: 'Chand.', Gadchiroli: 'Gadc.', Bhandara: 'Bhand.',
}

export default function MapView() {
  const mapData = useStore((s) => s.mapData)
  const popups = useStore((s) => s.ui.popups)
  const openDistrict = useStore((s) => s.openDistrict)
  const closePopup = useStore((s) => s.closePopup)
  const ranking = useStore((s) => s.state?.ranking)
  const changeKind = useStore((s) => s.changeKind)
  const byId = useMemo(() => Object.fromEntries((mapData?.districts || []).map((d) => [d.id, d])), [mapData])
  const clusterMode = useStore((s) => s.ui.clusterMode)
  const clusters = useStore((s) => s.ui.clusters)
  const fills = useMemo(() => tierFills(ranking), [ranking])
  // §10 cluster mode: districts keep their rank colors and get an outline in their cluster's color.
  const clusterStroke = useMemo(
    () => (clusters ? Object.fromEntries(clusters.clusters.flatMap((c) => c.district_ids.map((id) => [id, c.color]))) : null),
    [clusters],
  )
  const outlines = clusterMode && clusterStroke ? clusterStroke : null
  const entries = useMemo(() => Object.fromEntries((ranking || []).map((e) => [e.district_id, e])), [ranking])
  // Recolor wave: each district waits by its rank, so #1 lights up first and the rest follow.
  const delays = useMemo(() => {
    const n = (ranking || []).filter((e) => e.eligible).length
    return Object.fromEntries((ranking || []).map((e) => [e.district_id, (e.eligible ? e.rank - 1 : n) * WAVE_STEP_MS]))
  }, [ranking])
  const leader = (ranking || []).find((e) => e.eligible && e.rank === 1)

  // P6 gate: time from a new ranking (chat response or slider drag) until the new fills are in
  // the DOM (layout effect: after the DOM update, before paint), plus one frame and the fill
  // transition. The wave makes a full recolor longer than the original 300 ms gate on purpose.
  useLayoutEffect(() => {
    const perf = globalThis.__gcPerf
    const p = perf?.pending
    if (!p || !ranking) return
    perf.pending = null
    const commit = performance.now() - p.t
    const fill = p.kind === 'slider' ? SLIDER_FILL_MS : FILL_TRANSITION_MS + Math.max(0, ...Object.values(delays))
    const rec = { commit_ms: Math.round(commit), est_total_ms: Math.round(commit + FRAME_MS + fill), paint_ms: null }
    perf[p.kind]?.push(rec)
    requestAnimationFrame(() => (rec.paint_ms = Math.round(performance.now() - p.t)))
  }, [ranking, delays])

  const wrapRef = useRef(null)
  const svgRef = useRef(null)
  const drag = useRef(null)
  const suppressClick = useRef(false)
  const [view, setView] = useState({ k: 1, tx: 0, ty: 0 })
  const [smooth, setSmooth] = useState(false) // zoom buttons and reset glide; wheel and drag follow the pointer
  const [hover, setHover] = useState(null)
  const [panning, setPanning] = useState(false)
  // Stable click handler so the memoized district layer only re-renders when fills change.
  const openRef = useRef(null)
  const onOpen = useCallback((id, ev) => openRef.current(id, ev), [])

  const vb = mapData ? mapData.viewBox.split(' ').map(Number) : [0, 0, 900, 750]
  const [, , VW, VH] = vb
  const intro = useMemo(
    () => Object.fromEntries((mapData?.districts || []).map((d) => [d.id, Math.round((d.label_x / VW) * INTRO_SWEEP_MS)])),
    [mapData, VW],
  )

  // Keep the map partly on screen: never pan more than the zoomed overflow plus a margin.
  const clampView = useCallback(
    ({ k, tx, ty }) => {
      const m = 0.25
      return {
        k,
        tx: clamp(tx, -(k - 1) * VW - VW * m, VW * m),
        ty: clamp(ty, -(k - 1) * VH - VH * m, VH * m),
      }
    },
    [VW, VH],
  )

  // Client pixels -> viewBox units.
  const toSvg = (clientX, clientY) => {
    const ctm = svgRef.current.getScreenCTM()
    return { x: (clientX - ctm.e) / ctm.a, y: (clientY - ctm.f) / ctm.d }
  }

  const zoomAt = useCallback(
    (factor, px, py) => {
      setView((v) => {
        const k = clamp(v.k * factor, MIN_K, MAX_K)
        const wx = (px - v.tx) / v.k
        const wy = (py - v.ty) / v.k
        return clampView({ k, tx: px - wx * k, ty: py - wy * k })
      })
    },
    [clampView],
  )

  // Wheel zoom needs a non-passive listener so the page never scrolls.
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const onWheel = (e) => {
      e.preventDefault()
      setSmooth(false)
      const p = toSvg(e.clientX, e.clientY)
      zoomAt(Math.exp(-e.deltaY * 0.0015), p.x, p.y)
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  }, [zoomAt, mapData])

  // Escape closes the newest popup (unless a modal is open; it handles Escape itself).
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && !useStore.getState().ui.modal && closePopup()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [closePopup])

  if (!mapData) return <div className="map-loading muted">Loading map…</div>

  const onPointerDown = (e) => {
    if (e.button !== 0) return
    drag.current = { x: e.clientX, y: e.clientY, view, moved: false, id: e.pointerId }
  }
  const onPointerMove = (e) => {
    const d = drag.current
    if (d) {
      const dx = e.clientX - d.x
      const dy = e.clientY - d.y
      if (!d.moved && Math.hypot(dx, dy) > DRAG_PX) {
        d.moved = true
        setSmooth(false)
        setPanning(true)
        setHover(null)
        svgRef.current.setPointerCapture(d.id)
      }
      if (d.moved) {
        const s = svgRef.current.getScreenCTM().a
        setView(clampView({ k: d.view.k, tx: d.view.tx + dx / s, ty: d.view.ty + dy / s }))
      }
      return
    }
    const id = Number(e.target.dataset?.id)
    if (id) {
      const box = wrapRef.current.getBoundingClientRect()
      setHover({ id, x: e.clientX - box.left, y: e.clientY - box.top })
    } else if (hover) {
      setHover(null)
    }
  }
  const onPointerUp = () => {
    if (drag.current?.moved) suppressClick.current = true
    drag.current = null
    setPanning(false)
  }

  const open = (id, e) => {
    if (suppressClick.current) {
      suppressClick.current = false
      return
    }
    openDistrict(id, e.type === 'keydown')
    setHover(null)
  }

  openRef.current = open

  // A pan ends with a click on whatever is under the pointer; swallow it. Popups float and stay open.
  const onBackgroundClick = () => {
    suppressClick.current = false
  }

  const reset = () => {
    setSmooth(true)
    setView({ k: 1, tx: 0, ty: 0 })
  }
  const zoomCenter = (f) => {
    setSmooth(true)
    zoomAt(f, VW / 2, VH / 2)
  }
  const fs = FS / Math.sqrt(view.k)
  const mumbaiDetail = view.k >= MUMBAI_DETAIL_K
  const hoverName = hover && byId[hover.id]?.name

  return (
    <div className="map-wrap" ref={wrapRef}>
      <svg
        ref={svgRef}
        className={`map${panning ? ' panning' : ''}${changeKind === 'slider' ? '' : ' wave'}`}
        viewBox={mapData.viewBox}
        role="group"
        aria-label="Map of Maharashtra districts. Drag to pan, scroll to zoom."
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={() => !drag.current && setHover(null)}
        onClick={onBackgroundClick}
      >
        <defs>
          <pattern id="hatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
            <rect width="6" height="6" className="hatch-bg" />
            <line x1="0" y1="0" x2="0" y2="6" className="hatch-line" />
          </pattern>
        </defs>
        <rect x={-VW * 5} y={-VH * 5} width={VW * 11} height={VH * 11} className="map-bg" />
        <g
          className={`zoom-layer${smooth ? ' smooth' : ''}`}
          style={{ transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.k})`, '--stroke': 0.8 / view.k }}
        >
          <g className="regions" style={{ fontSize: 9 / Math.sqrt(view.k) }}>
            {(mapData.regions || []).map((r) => (
              <text key={r.name} x={r.x} y={r.y} className="region-label">{r.name}</text>
            ))}
          </g>
          <DistrictLayer districts={mapData.districts} fills={fills} entries={entries} selected={popups} onOpen={onOpen}
            outlines={outlines} delays={delays} intro={intro} />
          {leader && <path key={leader.district_id} d={byId[leader.district_id].d} className="leader-ring" aria-hidden="true" />}
          <LabelLayer districts={mapData.districts} byId={byId} entries={entries} fs={fs} mumbaiDetail={mumbaiDetail}
            delays={delays} />
          <g className="rank-badges" style={{ fontSize: fs * 1.05 }}>
            {(ranking || [])
              .filter((e) => e.eligible && e.rank <= 5)
              .map((e) => {
                const d = byId[e.district_id]
                // Keyed by rank, not district: when the order changes, "#1" glides to the new leader.
                return (
                  <g key={e.rank} className="rank-badge" style={{ transform: `translate(${d.label_x}px, ${d.label_y}px)` }}>
                    <text y={-fs * 1.25} data-badge={e.rank} className={`rank-text${e.rank === 1 ? ' first' : ''}`}>
                      #{e.rank}
                    </text>
                  </g>
                )
              })}
          </g>
        </g>
      </svg>

      {hover && !panning && (
        <div className="map-tooltip" style={{ left: hover.x + 14, top: hover.y - 12 }}>
          {hoverName}
          <TipDetail e={entries[hover.id]} />
        </div>
      )}

      <div className="zoom-controls" role="group" aria-label="Zoom">
        <button onClick={() => zoomCenter(1.5)} aria-label="Zoom in">+</button>
        <button onClick={() => zoomCenter(1 / 1.5)} aria-label="Zoom out">−</button>
        <button onClick={reset} aria-label="Reset view" disabled={view.k === 1 && !view.tx && !view.ty}>⟲</button>
      </div>
    </div>
  )
}

// The 36 district shapes. Memoized: hover, pan and zoom never re-render them; only new fills,
// eligibility, cluster outlines or the open popups do. Hover highlight is CSS (:hover), not React state.
const DistrictLayer = memo(function DistrictLayer({ districts, fills, entries, selected, onOpen, outlines, delays, intro }) {
  const open = new Set(selected.map((p) => p.id))
  return districts.map((d) => {
    const e = entries[d.id]
    const fill = e && !e.eligible ? 'url(#hatch)' : fills[d.id]
    const outline = outlines?.[d.id]
    const style = {
      '--d': `${delays[d.id] || 0}ms`,
      '--i': `${intro[d.id] || 0}ms`,
      ...(fill ? { fill } : {}),
      ...(outline ? { stroke: outline } : {}),
    }
    return (
      <path
        key={d.id}
        d={d.d}
        className={`district${open.has(d.id) ? ' selected' : ''}${e && !e.eligible ? ' ineligible' : ''}${outline ? ' clustered' : ''}`}
        data-id={d.id}
        data-fill={fill || ''}
        data-stroke={outline || ''}
        tabIndex={0}
        role="button"
        aria-label={tipText(d.name, e)}
        style={style}
        onClick={(ev) => onOpen(d.id, ev)}
        onKeyDown={(ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault()
            onOpen(d.id, ev)
          }
        }}
      />
    )
  })
})

const LabelLayer = memo(function LabelLayer({ districts, byId, entries, fs, mumbaiDetail, delays }) {
  return (
    <g className="labels" style={{ fontSize: fs }}>
      {districts
        .filter((d) => !MUMBAI.has(d.id) || mumbaiDetail)
        .map((d) => {
          const e = entries[d.id]
          const r = e?.eligible ? e.rank : null
          return (
            <text key={d.id} x={d.label_x} y={d.label_y} style={{ '--d': `${delays[d.id] || 0}ms` }}
              className={`district-label${r && r <= 3 ? ' on-bright' : ''}${r && r <= 5 ? ' top' : ''}`}>
              {SHORT_NAMES[d.name] || d.name}
            </text>
          )
        })}
      {!mumbaiDetail && <MumbaiLabel city={byId[1]} suburban={byId[2]} fs={fs} />}
    </g>
  )
})

function TipDetail({ e }) {
  if (!e) return null
  if (!e.eligible) return <span className="tip-detail warn">not eligible · {reasonList(e.reason).join(', ')}</span>
  return <span className="tip-detail mono">#{e.rank} · {e.score.toFixed(1)} pts</span>
}

// "Name · #rank · score · confidence" (§12.4); ineligible districts give the reason.
function tipText(name, e) {
  if (!e) return name
  if (!e.eligible) return `${name} · not eligible: ${reasonList(e.reason).join(', ')}`
  return `${name} · #${e.rank} · ${e.score.toFixed(1)} · ${e.confidence} confidence`
}

// At normal zoom the two Mumbai districts are a few pixels wide; one "Mumbai" label
// sits just offshore with a leader line so the city is visible on the map itself.
function MumbaiLabel({ city, suburban, fs }) {
  const ax = (city.label_x + suburban.label_x) / 2
  const ay = (city.label_y + suburban.label_y) / 2
  const lx = ax - fs * 3.2
  return (
    <g className="mumbai-label">
      <line x1={ax - 1.5} y1={ay} x2={lx + fs * 0.4} y2={ay} />
      <circle cx={ax} cy={ay} r={fs * 0.22} />
      <text x={lx} y={ay} textAnchor="end" className="district-label strong">
        Mumbai
      </text>
    </g>
  )
}
