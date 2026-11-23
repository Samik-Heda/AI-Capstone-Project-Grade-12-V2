import { useRef } from 'react'
import { importanceWords, toPos, toWeight } from '../lib/sliders.js'

export const CHANGED_EPS = 0.004
const KEY_STEP = 0.05
const MOD_MARK = { critical: '↑↑', high: '↑', low: '↓', exclude: '✕' }

// One visual slider (§5.5): no numbers and no input box. Position, fill and fill opacity show
// how much the factor matters; a faint tick marks the preset's position.
export default function Slider({ field, weight, base, max, changed, mod, pulse, active, onDrag, onEnd }) {
  const trackRef = useRef(null)
  const dragging = useRef(false)
  const pos = toPos(weight)
  const fromEvent = (e) => {
    const r = trackRef.current.getBoundingClientRect()
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))
  }
  const onPointerDown = (e) => {
    if (e.button !== 0) return
    dragging.current = true
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      /* synthetic pointers cannot be captured; moves still arrive on the track */
    }
    onDrag(toWeight(fromEvent(e)))
  }
  const onPointerMove = (e) => dragging.current && onDrag(toWeight(fromEvent(e)))
  const onPointerUp = () => {
    if (!dragging.current) return
    dragging.current = false
    onEnd()
  }
  const onKeyDown = (e) => {
    const d = { ArrowRight: KEY_STEP, ArrowUp: KEY_STEP, ArrowLeft: -KEY_STEP, ArrowDown: -KEY_STEP }[e.key]
    if (d === undefined) return
    e.preventDefault()
    onDrag(toWeight(Math.min(1, Math.max(0, pos + d))))
    onEnd()
  }
  const strength = max > 0 ? weight / max : 0

  return (
    <div key={pulse || undefined} data-field={field.key}
      className={`slider-row${changed ? ' changed' : ''}${active ? ' active' : ''}${pulse ? ' pulse-amber' : ''}`}>
      <div className="slider-label">
        <span className="has-tip" tabIndex={-1}>
          {field.plain_label}
          <span className="tip">
            <b>{field.label}</b>: higher means {field.explain_high}.
            {field.status === 'Estimated' && <em> Estimated value.</em>}
          </span>
        </span>
        {mod && <span className="mod-mark" title="Changed from chat">{MOD_MARK[mod]}</span>}
      </div>
      <div
        ref={trackRef}
        className="slider-track"
        role="slider"
        tabIndex={0}
        aria-label={field.plain_label}
        aria-valuetext={importanceWords(weight, max)}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onKeyDown={onKeyDown}
      >
        <div className="slider-fill" style={{ width: `${pos * 100}%`, opacity: 0.35 + 0.65 * strength }} />
        {changed && <div className="slider-tick" style={{ left: `${toPos(base) * 100}%` }} />}
        <div className="slider-thumb" style={{ left: `${pos * 100}%` }} />
      </div>
    </div>
  )
}
