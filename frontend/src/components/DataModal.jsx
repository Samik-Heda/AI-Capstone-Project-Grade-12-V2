import { useState } from 'react'
import { useStore } from '../store.js'
import { useDialog } from '../lib/useDialog.js'
import BenchmarkReport from './BenchmarkReport.jsx'

// Old-style "Data sources" card, filled from the field registry (§4.3) so every factor shows its
// true provenance status (§17), plus the known limitations (§4.7) and the last benchmark run (§16 step 9).
function Section({ title, children, testid }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="data-section" data-testid={testid}>
      <button className="section-btn" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span>{title}</span>
        <span className="dim">{open ? '▴' : '▸'}</span>
      </button>
      {open && <div className="section-body">{children}</div>}
    </div>
  )
}

export default function DataModal() {
  const meta = useStore((s) => s.meta)
  const setUi = useStore((s) => s.setUi)
  const close = () => setUi({ modal: null })
  const ref = useDialog(close)

  const fields = meta?.fields || []
  const ports = meta?.ports || []
  const about = meta?.about || {}
  const limitations = about.limitations || []
  const counts = fields.reduce((a, f) => ({ ...a, [f.status]: (a[f.status] || 0) + 1 }), {})
  const year = (f) => f.year || f.source?.match(/\b(19|20)\d{2}(?:[–-]\d{2,4})?\b/)?.[0] || '—'

  return (
    <div className="backdrop" onPointerDown={(e) => e.target === e.currentTarget && close()}>
      <div className="modal data-modal" role="dialog" aria-modal="true" aria-label="Data sources" ref={ref}>
        <div className="modal-head">
          <h2>Data sources</h2>
          <button className="icon-btn" onClick={close} aria-label="Close">×</button>
        </div>
        <div className="modal-scroll">
          <p className="intro">
            {meta?.districts?.length ?? 36} districts × {fields.length} factors.{' '}
            {Object.entries(counts).map(([k, v], i) => (
              <span key={k}>
                {i > 0 && ', '}
                <span className={`status s-${k.toLowerCase()}`}>{k}</span> {v}
              </span>
            ))}
            . Estimated values are marked ◌ in the district popups.
          </p>
          {meta?.status_note && <p className="note">{meta.status_note}</p>}

          <table className="data-table">
            <thead>
              <tr>
                <th>Factor</th>
                <th>Source</th>
                <th>Year</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {fields.map((f) => (
                <tr key={f.key} data-field={f.key}>
                  <td title={`${f.label}${f.unit ? ` (${f.unit})` : ''}`}>{f.plain_label}</td>
                  <td className="muted">{f.source}</td>
                  <td className="mono dim">{year(f)}</td>
                  <td><span className={`status s-${f.status.toLowerCase()}`}>{f.status}</span></td>
                </tr>
              ))}
              {(meta?.display_only || []).map((f) => (
                <tr key={f.key}>
                  <td>{f.label} <span className="dim">(shown only)</span></td>
                  <td className="muted">{f.source}</td>
                  <td className="mono dim">{year(f)}</td>
                  <td><span className={`status s-${f.status.toLowerCase()}`}>{f.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>

          {ports.length > 0 && (
            <div className="ports-box">
              <b>Ports used for the distance to a port:</b>
              <br />
              {ports.map((p) => p.name + (p.district ? ` (${p.district})` : '')).join(' · ')}
            </div>
          )}

          <Section title="How to read the status">
            <ul className="muted">
              <li><b className="s-sourced">Sourced</b>: taken directly from a published dataset.</li>
              <li><b className="s-derived">Derived</b>: computed in code from sourced data (for example, distance to the nearest major port from district map shapes).</li>
              <li><b className="s-estimated">Estimated</b>: an approximation for demonstration. Check it before a real investment decision.</li>
            </ul>
          </Section>
          <Section title={`Known limitations (${limitations.length})`} testid="limitations-section">
            {limitations.length ? (
              <ul className="muted" data-testid="limitations">
                {limitations.map((t) => <li key={t}>{t}</li>)}
              </ul>
            ) : (
              <p className="muted small">Not available from this server. Restart it with start.bat to load them.</p>
            )}
          </Section>
          {(about.about || []).length > 0 && (
            <Section title="About">
              {about.about.map((t) => <p key={t} className="muted">{t}</p>)}
            </Section>
          )}
          <Section title="How well it works">
            <BenchmarkReport />
          </Section>
        </div>
      </div>
    </div>
  )
}
