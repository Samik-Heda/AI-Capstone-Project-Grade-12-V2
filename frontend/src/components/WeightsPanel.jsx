import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../store.js'
import { rebalance } from '../lib/sliders.js'
import Slider, { CHANGED_EPS } from './Slider.jsx'

export default function WeightsPanel() {
  const state = useStore((s) => s.state)
  const fields = useStore((s) => s.meta?.fields)
  const open = useStore((s) => s.ui.weightsOpen)
  const setUi = useStore((s) => s.setUi)
  const setWeights = useStore((s) => s.setWeights)
  const reset = useStore((s) => s.reset)
  const send = useStore((s) => s.send)
  // Transient drag preview; cleared once the server state carries the dragged value.
  const [drag, setDrag] = useState(null) // {field, value, settling}
  // Rows changed from chat ("water is critical") pulse amber once (old-project animation).
  const [pulse, setPulse] = useState({ fields: [], n: 0 })
  const prevMods = useRef(null)
  const modifiers = state?.modifiers

  const profileId = state?.profile?.id
  const baseWeights = state?.base_weights
  // Sorted by importance when the panel opens or the profile/base changes, then frozen, so
  // rows never jump under the pointer while dragging.
  const order = useMemo(() => {
    if (!fields || !state?.weights) return []
    return [...fields].sort((a, b) => state.weights[b.key] - state.weights[a.key] || a.plain_label.localeCompare(b.plain_label))
  }, [fields, open, profileId, baseWeights])

  useEffect(() => {
    if (!drag?.settling) return
    const o = state?.slider_overrides?.[drag.field]
    if (o !== undefined && Math.abs(o - drag.value) < 1e-6) setDrag(null)
  }, [state, drag])
  useEffect(() => {
    if (!drag?.settling) return
    const t = setTimeout(() => setDrag(null), 1500)
    return () => clearTimeout(t)
  }, [drag])

  useEffect(() => {
    const prev = prevMods.current
    prevMods.current = modifiers
    if (!prev || !modifiers) return
    const changedNow = Object.keys(modifiers).filter((k) => modifiers[k] !== prev[k])
    if (changedNow.length) setPulse((p) => ({ fields: changedNow, n: p.n + 1 }))
  }, [modifiers])

  if (!state?.profile || !fields) return null

  const shown = drag ? rebalance(state.weights, state.slider_overrides, drag.field, drag.value) : state.weights
  const max = Math.max(...Object.values(shown))
  const isChanged = (k) => Math.abs(shown[k] - state.base_weights[k]) > CHANGED_EPS
  const anyChanged =
    Object.keys(state.modifiers).length > 0 ||
    Object.keys(state.slider_overrides).length > 0 ||
    state.constraints.requires_industrial_land === false
  const floors = Object.keys(state.constraints.min_norm || {})
  const plain = Object.fromEntries(fields.map((f) => [f.key, f.plain_label.toLowerCase()]))

  return (
    <div className={`weights-panel${open ? ' open' : ''}`} role="region" aria-label="Adjust priorities">
      <button className="overlay-head head-btn" onClick={() => setUi({ weightsOpen: !open })} aria-expanded={open}>
        <span>⚖ Weights — {state.profile.name}</span>
        <span className="head-right">
          {anyChanged && <span className="pill-dot" aria-label="changed" />}
          <span className="dim">{open ? '▴ hide' : '▾ show'}</span>
        </span>
      </button>
      {open && (
        <div className="weights-body">
          <div className="slider-ends muted small">
            <span>Doesn't matter</span>
            <span>Matters most</span>
          </div>
          <div className={`sliders${drag ? ' dragging' : ''}`}>
            {order.map((f) => (
              <Slider
                key={f.key}
                field={f}
                weight={shown[f.key]}
                base={state.base_weights[f.key]}
                max={max}
                changed={isChanged(f.key)}
                mod={state.modifiers[f.key]}
                pulse={pulse.fields.includes(f.key) ? pulse.n : 0}
                active={drag?.field === f.key}
                onDrag={(value) => {
                  setDrag({ field: f.key, value, settling: false })
                  setWeights({ [f.key]: value })
                }}
                onEnd={() => setDrag((d) => (d ? { ...d, settling: true } : d))}
              />
            ))}
          </div>
          <button
            className="toggle"
            role="switch"
            aria-checked={state.constraints.requires_industrial_land}
            onClick={() => setWeights({}, { requires_industrial_land: !state.constraints.requires_industrial_land })}
          >
            <span className="toggle-ui" aria-hidden="true" />
            Must be in an industrial area
          </button>
          {floors.length > 0 && (
            <p className="muted small">This industry also needs enough {floors.map((k) => plain[k]).join(' and ')}.</p>
          )}
          <div className="panel-actions">
            <button className="btn" onClick={reset} disabled={!anyChanged}>
              Reset
            </button>
            {anyChanged && (
              <button className="btn primary" onClick={() => send('Save as my profile')}>
                Save as my profile
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
