import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'

// Headline numbers and the full gate table of the last `run_benchmarks.py` run (§16 step 9).
// Everything here is read from the harness output; the app computes none of it.
export default function BenchmarkReport() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    api.get('/api/benchmarks').then(setData, (e) => setError(e.message))
  }, [])

  if (error) return <p className="muted small">Benchmark summary unavailable: {error}</p>
  if (!data) return <div className="skeleton-line" aria-label="Loading benchmark summary" />
  if (!data.available) return <p className="muted small" data-testid="bench-summary">{data.message}</p>

  return (
    <div className="bench" data-testid="bench-summary">
      <p className="muted small">
        Last benchmark run {data.timestamp}: {data.passed}/{data.total} gates passed
        {data.llm ? ' (with the live AI model)' : ' (without the AI model)'}.
      </p>
      <div className="bench-tiles">
        {data.headline.map((h) => (
          <div key={h.label} className={`bench-tile${h.pass ? '' : ' fail'}`}>
            <div className="bench-value">{h.value}</div>
            <div className="bench-label">{h.label}</div>
            <div className="bench-target muted">target {h.target}</div>
          </div>
        ))}
      </div>
      <button className="link-btn" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? 'Hide' : 'Show'} the full report ({data.total} gates)
      </button>
      {open && (
        <table className="data-table bench-table">
          <thead>
            <tr>
              <th>Phase</th>
              <th>Metric</th>
              <th>Result</th>
              <th>Target</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.gates.map((g, i) => (
              <tr key={i} className={g.passed ? '' : 'fail'}>
                <td>P{g.phase}</td>
                <td>{g.metric}</td>
                <td className="muted">{String(g.value)}</td>
                <td className="muted">{g.target}</td>
                <td>{g.passed ? '✓' : '✗'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
