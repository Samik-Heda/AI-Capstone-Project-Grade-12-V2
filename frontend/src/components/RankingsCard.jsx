import { Fragment, useLayoutEffect, useRef } from 'react'
import { useStore } from '../store.js'
import { tierColor } from '../lib/color.js'

const MOVE_MS = 480

// When the order changes, rows slide from where they were to where they are now (FLIP), and rows
// that just entered fade in. A row caught mid-slide continues from where it is on screen.
function useRowMotion(listRef, order) {
  const prevTop = useRef(new Map())
  useLayoutEffect(() => {
    const list = listRef.current
    if (!list) return
    const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const seen = prevTop.current.size > 0
    const next = new Map()
    for (const el of list.querySelectorAll('[data-district]')) {
      const id = el.dataset.district
      const top = el.offsetTop
      next.set(id, top)
      if (calm || !el.animate) continue
      const prev = prevTop.current.get(id)
      if (prev === undefined) {
        if (seen) el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 320, easing: 'ease' })
        continue
      }
      const inFlight = new DOMMatrixReadOnly(getComputedStyle(el).transform).m42
      const dy = prev - top + inFlight
      if (Math.abs(dy) < 1) continue
      el.getAnimations().forEach((a) => a.cancel())
      el.animate([{ transform: `translateY(${dy}px)` }, { transform: 'none' }],
        { duration: MOVE_MS, easing: 'cubic-bezier(0.3, 0.7, 0.2, 1)' })
    }
    prevTop.current = next
  }, [listRef, order])
}

// Old-style rankings overlay: the top 10, one click opens the district.
// "Show all 36" from chat (action:show_all) expands it; the link collapses it again.
export default function RankingsCard() {
  const state = useStore((s) => s.state)
  const all = useStore((s) => s.ui.rankingsAll)
  const busy = useStore((s) => s.ui.busy)
  const popups = useStore((s) => s.ui.popups)
  const setUi = useStore((s) => s.setUi)
  const openDistrict = useStore((s) => s.openDistrict)
  const listRef = useRef(null)
  const eligible = state?.ranking?.filter((e) => e.eligible) || []
  const rows = all ? eligible : eligible.slice(0, 10)
  useRowMotion(listRef, rows.map((e) => e.district_id).join(','))

  if (!state?.profile) {
    // First ranking on its way: shimmer rows like the old app.
    if (!busy) return null
    return (
      <div className="rankings-card" aria-label="Rankings" aria-busy="true">
        <div className="overlay-head">Rankings</div>
        {Array.from({ length: 10 }, (_, i) => <div key={i} className="rank-skeleton shimmer" />)}
      </div>
    )
  }

  const top = eligible[0]?.score || 1
  const open = new Set(popups.map((p) => p.id))

  return (
    <div className={`rankings-card${all ? ' expanded' : ''}`} aria-label="Rankings">
      <div className="overlay-head">Rankings</div>
      <ol className="rank-list" ref={listRef}>
        {rows.map((e, i) => (
          <Fragment key={e.district_id}>
            {i === 5 && <li className="rank-divider" aria-hidden="true" />}
            <li>
              <button
                className={`rank-row${open.has(e.district_id) ? ' active' : ''}`}
                data-district={e.district_id}
                onClick={() => openDistrict(e.district_id)}
              >
                <span className="rank-num">#{e.rank}</span>
                <span className="rank-name">{e.name}</span>
                <span className="rank-bar">
                  <span style={{ width: `${(100 * e.score) / top}%`, background: tierColor(e.rank) }} />
                </span>
                <span className="rank-score">{e.score.toFixed(0)}</span>
              </button>
            </li>
          </Fragment>
        ))}
      </ol>
      {all && (
        <button className="link-btn overlay-link" onClick={() => setUi({ rankingsAll: false })}>
          Show top 10
        </button>
      )}
    </div>
  )
}
