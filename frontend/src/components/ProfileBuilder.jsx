import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../store.js'
import { api } from '../lib/api.js'
import { rebalance } from '../lib/sliders.js'
import { useDialog } from '../lib/useDialog.js'
import Slider, { CHANGED_EPS } from './Slider.jsx'

// §9.2 Profile builder: one sentence → starting preset + visual sliders, a live top-5 preview,
// then Save / Use now. Sliders carry no numbers; what the text moved is highlighted.
const TYPE_PAUSE_MS = 500
const PREVIEW_DEBOUNCE_MS = 150
const PLACEHOLDER = 'e.g. small tyre factory, water is very important, land price doesn’t matter'

// Presets are stored sparse; sliders need every factor, summing to 1.
function fullWeights(fields, w) {
  const out = Object.fromEntries(fields.map((f) => [f.key, w?.[f.key] || 0]))
  const sum = Object.values(out).reduce((a, b) => a + b, 0) || 1
  for (const k in out) out[k] /= sum
  return out
}

export default function ProfileBuilder() {
  const modal = useStore((s) => s.ui.modal)
  const fields = useStore((s) => s.meta.fields)
  const presets = useStore((s) => s.profiles.preset)
  const setUi = useStore((s) => s.setUi)
  const refreshProfiles = useStore((s) => s.refreshProfiles)
  const setProfile = useStore((s) => s.setProfile)

  const sortedPresets = useMemo(() => [...presets].sort((a, b) => a.name.localeCompare(b.name)), [presets])
  const firstStart = modal.start && presets.some((p) => p.id === modal.start) ? modal.start : null

  const [text, setText] = useState(modal.text || '')
  const [startId, setStartId] = useState(firstStart) // null → picked from the text
  const [draft, setDraft] = useState(null) // last /profiles/draft response (or a plain preset)
  const [overrides, setOverrides] = useState({})
  const [industrial, setIndustrial] = useState(true)
  const [name, setName] = useState('')
  const [nameTouched, setNameTouched] = useState(false)
  const [preview, setPreview] = useState([])
  const [status, setStatus] = useState({ busy: false, error: null, saved: null })
  const [drag, setDrag] = useState(null)
  const reqSeq = useRef(0)
  const textRef = useRef(null)

  const close = useCallback(() => setUi({ modal: null }), [setUi])
  const dialogRef = useDialog(close, textRef)

  // text and/or Start from → draft. Without text the sliders are simply the preset.
  const generate = useCallback(
    async (t, start) => {
      const seq = ++reqSeq.current
      const clean = t.trim()
      try {
        let d
        if (clean) {
          setStatus((s) => ({ ...s, busy: true, error: null }))
          d = await api.post('/api/profiles/draft', start ? { text: clean, base_preset_id: start } : { text: clean })
        } else {
          const p = presets.find((x) => x.id === (start || 'preset:general_manufacturing'))
          const w = fullWeights(fields, p.weights)
          d = { base_preset_id: p.id, auto_preset_id: null, modifiers: {}, base_weights: w, weights: w,
                constraints: { requires_industrial_land: true, ...p.constraints }, preset_tier: 'menu',
                suggested_name: `${p.name} (custom)`, rationale_draft: `Based on ${p.name}.` }
        }
        if (seq !== reqSeq.current) return
        setDraft(d)
        setOverrides({})
        setIndustrial(d.constraints.requires_industrial_land !== false)
        if (!nameTouched) setName(d.suggested_name)
        setStatus((s) => ({ ...s, busy: false, saved: null }))
      } catch (e) {
        if (seq === reqSeq.current) setStatus((s) => ({ ...s, busy: false, error: e.message }))
      }
    },
    [presets, fields, nameTouched],
  )

  // first load, then after a pause in typing
  useEffect(() => {
    const t = setTimeout(() => generate(text, startId), draft ? TYPE_PAUSE_MS : 0)
    return () => clearTimeout(t)
  }, [text])

  const weights = useMemo(() => {
    if (!draft) return null
    let w = draft.weights
    const applied = {}
    for (const [k, v] of Object.entries(overrides)) {
      w = rebalance(w, applied, k, v)
      applied[k] = v
    }
    return drag ? rebalance(w, applied, drag.field, drag.value) : w
  }, [draft, overrides, drag])

  const constraints = useMemo(
    () => (draft ? { ...draft.constraints, requires_industrial_land: industrial } : null),
    [draft, industrial],
  )

  // live top-5 preview (§9.2), debounced
  useEffect(() => {
    if (!weights || drag) return
    const t = setTimeout(async () => {
      try {
        const r = await api.post('/api/rank', { weights, constraints })
        setPreview(r.ranking.filter((e) => e.eligible).sort((a, b) => a.rank - b.rank).slice(0, 5))
      } catch {
        /* keep the last preview */
      }
    }, PREVIEW_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [weights, constraints, drag])

  // Sorted by importance when a draft arrives, then frozen so rows never jump while dragging.
  const order = useMemo(() => {
    if (!draft) return []
    return [...fields].sort((a, b) => draft.weights[b.key] - draft.weights[a.key] || a.plain_label.localeCompare(b.plain_label))
  }, [draft, fields])

  // What a save would store; "Use now" right after "Save" reuses that profile instead of saving a copy.
  const saveKey = weights ? JSON.stringify([name.trim(), weights, constraints, text.trim()]) : null

  const save = async (useNow) => {
    if (!weights || !name.trim()) return
    setStatus((s) => ({ ...s, busy: true, error: null }))
    try {
      let profile = status.saved && status.savedKey === saveKey ? status.saved : null
      if (!profile) {
        const dragged = Object.keys(overrides).length > 0
        ;({ profile } = await api.post('/api/profiles', {
          name: name.trim(),
          weights,
          constraints,
          description: text.trim() || null,
          base_profile_id: draft.base_preset_id,
          provenance: { source: 'builder', text: text.trim(), modifiers: dragged ? {} : draft.modifiers, overrides },
        }))
        await refreshProfiles()
      }
      setStatus({ busy: false, error: null, saved: profile, savedKey: saveKey })
      if (useNow) {
        await setProfile(profile.id)
        close()
      }
    } catch (e) {
      setStatus((s) => ({ ...s, busy: false, error: e.message }))
    }
  }

  const presetName = (id) => presets.find((p) => p.id === id)?.name
  const autoNote = !draft
    ? ''
    : startId
      ? 'Chosen by you'
      : draft.preset_tier === 'menu'
        ? ''
        : draft.preset_tier === 'default'
          ? 'No industry recognised, so this starts from General Manufacturing'
          : 'Picked from your description'
  const max = weights ? Math.max(...Object.values(weights)) : 0
  // §9.2: highlight what the text (or a drag) moved, not every factor renormalization nudged
  const moved = new Set([...Object.keys(draft?.modifiers || {}), ...Object.keys(overrides), ...(drag ? [drag.field] : [])])
  const floors = Object.keys(draft?.constraints?.min_norm || {})
  const plain = Object.fromEntries(fields.map((f) => [f.key, f.plain_label.toLowerCase()]))

  return (
    <div className="backdrop" onPointerDown={(e) => e.target === e.currentTarget && close()}>
      <div className="modal builder" role="dialog" aria-modal="true" aria-label="Build my own profile" ref={dialogRef}>
        <div className="modal-head">
          <h2>Build my own profile</h2>
          <button className="icon-btn" onClick={close} aria-label="Close">×</button>
        </div>
        <div className="modal-scroll builder-body">
          <label className="builder-q" htmlFor="builder-text">What are you building?</label>
          <div className="builder-text-row">
            <textarea
              id="builder-text"
              ref={textRef}
              rows={2}
              value={text}
              placeholder={PLACEHOLDER}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  generate(text, startId)
                }
              }}
            />
            <button className="btn" onClick={() => generate(text, startId)} disabled={status.busy}>
              Generate
            </button>
          </div>

          <div className="builder-start">
            <label htmlFor="builder-start">Start from</label>
            <select
              id="builder-start"
              data-testid="builder-start"
              value={draft?.base_preset_id || ''}
              onChange={(e) => {
                setStartId(e.target.value)
                generate(text, e.target.value)
              }}
            >
              {sortedPresets.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            {autoNote && <span className="muted small">{autoNote}</span>}
          </div>

          <div className="builder-grid">
            <div className="builder-sliders">
              <div className="slider-ends muted small">
                <span>Doesn't matter</span>
                <span>Matters most</span>
              </div>
              <div className={`sliders${drag ? ' dragging' : ''}`}>
                {weights &&
                  order.map((f) => (
                    <Slider
                      key={f.key}
                      field={f}
                      weight={weights[f.key]}
                      base={draft.base_weights[f.key]}
                      max={max}
                      changed={moved.has(f.key) && Math.abs(weights[f.key] - draft.base_weights[f.key]) > CHANGED_EPS}
                      active={drag?.field === f.key}
                      onDrag={(value) => setDrag({ field: f.key, value })}
                      onEnd={() =>
                        setDrag((d) => {
                          if (d) setOverrides((o) => ({ ...o, [d.field]: d.value }))
                          return null
                        })
                      }
                    />
                  ))}
              </div>
              <button className="toggle" role="switch" aria-checked={industrial} onClick={() => setIndustrial(!industrial)}>
                <span className="toggle-ui" aria-hidden="true" />
                Must be in an industrial area
              </button>
              {floors.length > 0 && (
                <p className="muted small">{presetName(draft.base_preset_id)} also needs enough {floors.map((k) => plain[k]).join(' and ')}.</p>
              )}
            </div>
            <div className="builder-side">
              <div className="builder-preview" aria-live="polite">
                <div className="muted small">Top 5 districts</div>
                <ol>
                  {preview.map((e) => (
                    <li key={e.district_id} data-district={e.district_id}>{e.name}</li>
                  ))}
                </ol>
              </div>
              {draft?.rationale_draft && (
                <p className="muted small builder-why">
                  {Object.keys(overrides).length ? `Based on ${presetName(draft.base_preset_id)}, adjusted by hand.` : draft.rationale_draft}
                </p>
              )}
            </div>
          </div>

          <div className="builder-save">
            <label htmlFor="builder-name">Name</label>
            <input
              id="builder-name"
              value={name}
              maxLength={80}
              onChange={(e) => {
                setName(e.target.value)
                setNameTouched(true)
              }}
            />
            <button className="btn" onClick={() => save(false)} disabled={status.busy || !weights || !name.trim()}>
              Save
            </button>
            <button className="btn primary" onClick={() => save(true)} disabled={status.busy || !weights || !name.trim()}>
              Use now
            </button>
          </div>
          {status.saved && <p className="small ok-text">Saved as {status.saved.name}. Find it under My profiles.</p>}
          {status.error && <p className="small error-text">Could not save: {status.error}</p>}
        </div>
      </div>
    </div>
  )
}
