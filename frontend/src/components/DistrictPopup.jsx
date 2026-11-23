import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store.js'
import { api } from '../lib/api.js'
import { reasonList } from '../lib/reasons.js'

const W = 360
const GAP = 16
const CONF_CLASS = { High: 'c-high', Medium: 'c-med', Low: 'c-low' }

// Where a popup first appears: just right of the rankings card, the second one beside the first.
// On narrow windows the pair slides left over the rankings card so the two never overlap.
function startPos(slot) {
  const pane = document.querySelector('.map-pane')?.getBoundingClientRect()
  const paneLeft = pane?.left ?? 380
  const first = Math.max(paneLeft + GAP, Math.min(paneLeft + 252, window.innerWidth - 2 * W - 12 - GAP))
  const left = first + slot * (W + 12)
  return { x: Math.max(GAP, Math.min(left, window.innerWidth - W - GAP)), y: (pane?.top ?? 56) + GAP }
}

// Every factor as a compact bar (old style): label, points and how the district compares.
function Breakdown({ values, plain }) {
  const rows = [...values].sort((a, b) => b.points - a.points || plain[a.field].localeCompare(plain[b.field]))
  return (
    <div className="breakdown">
      <h4>Score breakdown</h4>
      <div className="bd-grid">
        {rows.map((v) => (
          <div key={v.field} className={`bd-item${v.weight === 0 ? ' minor' : ''}`} data-field={v.field}
            title={`${plain[v.field]}: ${v.display}`}>
            <div className="bd-line">
              <span className="bd-label">
                {plain[v.field]}
                {v.status === 'Estimated' && <span className="est" title="Estimated value"> ◌</span>}
              </span>
              <span className="bd-pts" data-points>{v.points.toFixed(1)}</span>
            </div>
            <div className="bd-bar"><span style={{ width: `${Math.min(100, v.norm * 100)}%` }} /></div>
            <span className="sr-only" data-display>{v.display}</span>
          </div>
        ))}
      </div>
      <p className="muted tiny">Bar: how the district compares with the others · ◌ estimated value</p>
    </div>
  )
}

// One floating, draggable district window (§12.5). Up to two can be open side by side.
export default function DistrictPopup({ id, slot, viaKey, z }) {
  const meta = useStore((s) => s.meta)
  const sessionId = useStore((s) => s.sessionId)
  const state = useStore((s) => s.state)
  const closePopup = useStore((s) => s.closePopup)
  const openDistrict = useStore((s) => s.openDistrict)
  const setWeights = useStore((s) => s.setWeights)
  const [req, setReq] = useState({ data: null, loading: false, error: null })
  const [attempt, setAttempt] = useState(0) // bumped by Retry
  const { data, loading, error } = req
  const [pos, setPos] = useState(() => startPos(slot))
  const [dragging, setDragging] = useState(false)
  const drag = useRef(null)
  const boxRef = useRef(null)
  const reqSeq = useRef(0)

  const district = meta?.districts.find((d) => d.id === id)
  const hasProfile = !!state?.profile

  // Re-fetch whenever the session's weights/constraints change: never stale (§12.5).
  useEffect(() => {
    if (!hasProfile) {
      setReq({ data: null, loading: false, error: null })
      return
    }
    const seq = ++reqSeq.current
    setReq((r) => ({ ...r, loading: true, error: null }))
    api
      .get(`/api/district/${id}?session_id=${sessionId}`)
      .then((d) => seq === reqSeq.current && setReq({ data: d, loading: false, error: null }))
      .catch((err) => seq === reqSeq.current && setReq({ data: null, loading: false, error: err.message }))
  }, [id, sessionId, hasProfile, state?.weights, state?.constraints, attempt])

  // Opened from the keyboard (Enter on a district): move focus into the popup.
  useEffect(() => {
    if (viaKey) boxRef.current?.focus()
  }, [viaKey])

  // Drag by the top bar; the popup stays inside the window.
  useEffect(() => {
    if (!dragging) return
    const move = (e) => {
      const d = drag.current
      const h = boxRef.current?.offsetHeight || 120
      setPos({
        x: Math.max(0, Math.min(window.innerWidth - W, d.px + e.clientX - d.mx)),
        y: Math.max(0, Math.min(window.innerHeight - Math.min(h, 80), d.py + e.clientY - d.my)),
      })
    }
    const up = () => setDragging(false)
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [dragging])

  if (!district) return null
  const fresh = data && data.district.id === id ? data : null
  const plain = Object.fromEntries(meta.fields.map((f) => [f.key, f.plain_label]))
  const e = fresh?.entry
  const total = state?.ranking?.filter((x) => x.eligible).length || meta.districts.length

  const onBarDown = (ev) => {
    if (ev.button !== 0 || ev.target.closest('button')) return
    ev.preventDefault()
    drag.current = { mx: ev.clientX, my: ev.clientY, px: pos.x, py: pos.y }
    setDragging(true)
  }

  return (
    <div
      ref={boxRef}
      className={`district-popup${loading ? ' refreshing' : ''}${dragging ? ' dragging' : ''}`}
      role="dialog"
      aria-label={`${district.name} details`}
      data-district={id}
      tabIndex={-1}
      style={{ left: pos.x, top: pos.y, width: W, zIndex: 60 + z }}
      onPointerDown={() => openDistrict(id)}
    >
      <div className="popup-bar" onPointerDown={onBarDown}>
        <span className="grip" aria-hidden="true">⠿</span>
        <button className="icon-btn" onClick={() => closePopup(id)} aria-label="Close">×</button>
      </div>

      <div className="popup-body">
        <div className="popup-head">
          <h2>{district.name}</h2>
          <p className="muted">
            {district.region}
            {hasProfile && <span className="dim"> · {state.profile.name}</span>}
          </p>
        </div>

        {!hasProfile && (
          <p className="muted popup-placeholder">
            {district.metro_core === 1 && <span className="popup-note">Fully urban: no industrial land bank. </span>}
            Pick an industry in the chat to see how {district.name} ranks.
          </p>
        )}

        {e && (
          <>
            {e.eligible ? (
              <div className="popup-rank" data-rank={e.rank} data-score={e.score}>
                <div className="rank-box">
                  <span className="rank-big">#{e.rank}</span>
                  <span className="of">of {total}</span>
                </div>
                <div className="rank-meta">
                  <div className="metric">
                    <div className="metric-line">
                      <span className="muted">Score</span>
                      <span><span className="score-num">{e.score.toFixed(1)}</span><span className="dim"> / 100</span></span>
                    </div>
                    <span className="score-bar"><span style={{ width: `${e.score}%` }} /></span>
                  </div>
                  <div className="band-line">
                    <span className="muted">likely {e.band[0]}–{e.band[1]}</span>
                    <span className={`conf-chip ${CONF_CLASS[e.confidence]}`}>{e.confidence} confidence</span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="popup-ineligible">
                <strong>Not eligible</strong>
                <ul className="inelig-list">
                  {reasonList(fresh.reason, true).map((r) => <li key={r}>{r}</li>)}
                </ul>
                {fresh.reason?.includes('no industrial land') && state.constraints.requires_industrial_land && (
                  <button className="link-btn" onClick={() => setWeights({}, { requires_industrial_land: false })}>
                    Include districts without industrial land
                  </button>
                )}
              </div>
            )}

            <div className="divider" />
            <div className="sw">
              <div>
                <h4 className="up">+ Strengths</h4>
                {fresh.strengths.length ? (
                  <ul data-list="strengths">
                    {fresh.strengths.map((s) => (
                      <li key={s.field}>
                        <b>{plain[s.field]}</b> <span className="muted">{s.display}</span>{' '}
                        <span className="pts up">+{s.points.toFixed(1)}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted small">{fresh.strengths_note}</p>
                )}
              </div>
              <div>
                <h4 className="down">− Weaknesses</h4>
                {fresh.weaknesses.length ? (
                  <ul data-list="weaknesses">
                    {fresh.weaknesses.map((s) => (
                      <li key={s.field}>
                        <b>{plain[s.field]}</b> <span className="muted">{s.display}</span>{' '}
                        <span className="pts down">−{s.lost.toFixed(1)}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted small">None that matter for this industry.</p>
                )}
              </div>
            </div>

            <div className="divider" />
            <Breakdown values={fresh.values} plain={plain} />
          </>
        )}
        {hasProfile && !e && !error && (
          <div className="popup-skeleton" aria-label="Loading district details" aria-busy="true">
            <div className="skeleton-line wide shimmer" />
            <div className="skeleton-line shimmer" />
            <div className="skeleton-line shimmer" />
            <div className="skeleton-line short shimmer" />
          </div>
        )}
        {hasProfile && !e && error && (
          <div className="popup-error" role="alert">
            <p>Could not load {district.name}: {error}</p>
            <button className="btn" onClick={() => setAttempt((n) => n + 1)}>Retry</button>
          </div>
        )}
      </div>
    </div>
  )
}
