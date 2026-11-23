import { useStore } from '../store.js'
import { useDialog } from '../lib/useDialog.js'

// §18.3 business brief: a printable one-page summary. The facts render at once; four short
// paragraphs fill in as the local AI writes them (each checked against the data, or replaced by the
// plain template text). "Print / Save as PDF" prints only this card.

function Paragraph({ sec }) {
  if (!sec) return null
  if (sec.status === 'waiting') return <p className="brief-wait shimmer">Waiting to be written…</p>
  if (sec.status === 'writing' && !sec.text) return <p className="brief-wait shimmer">Writing…</p>
  return (
    <p className={`brief-para${sec.source === 'ai' || sec.status === 'writing' ? ' ai' : ''}`}>
      {sec.text}
      {sec.status === 'writing' && <span className="cursor-blink no-print" aria-hidden="true">▊</span>}
    </p>
  )
}

const today = () => new Date().toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' })

export default function BriefModal() {
  const b = useStore((s) => s.brief)
  const closeBrief = useStore((s) => s.closeBrief)
  const ref = useDialog(closeBrief)
  const d = b?.data
  const secs = b?.sections || {}
  const writing = b?.status === 'streaming'
  const firstWait = writing && !Object.values(secs).some((x) => x.text)

  return (
    <div className="backdrop brief-backdrop" onPointerDown={(e) => e.target === e.currentTarget && closeBrief()}>
      <div className="modal brief-modal" role="dialog" aria-modal="true" aria-label="Business brief" ref={ref}
        data-testid="brief" data-status={b?.status}>
        <div className="modal-head no-print">
          <h2>Business brief</h2>
          <div className="brief-actions">
            <button className="btn" onClick={() => window.print()} disabled={!d} data-testid="brief-print">
              Print / Save as PDF
            </button>
            <button className="icon-btn" onClick={closeBrief} aria-label="Close">×</button>
          </div>
        </div>
        <div className="modal-scroll brief-body">
          {b?.status === 'error' && <p className="error-text">{b.error}</p>}
          {!d && b?.status !== 'error' && <p className="brief-wait shimmer">Gathering the facts…</p>}
          {d && (
            <>
              <header className="brief-title">
                <div className="brief-brand">◈ GrowthCompass · Maharashtra district brief</div>
                <h1>{d.profile}</h1>
                <div className="dim small">{today()} · {d.eligible} districts ranked{d.excluded ? `, ${d.excluded} excluded` : ''}</div>
              </header>
              {firstWait && (
                <p className="brief-note no-print">
                  Preparing the writer. The first time can take about a minute; the facts below are ready now.
                </p>
              )}

              <section>
                <h3>Summary</h3>
                <Paragraph sec={secs.summary} />
              </section>

              <section className="brief-cols">
                <div>
                  <h3>What matters for this business</h3>
                  <ul className="brief-list">
                    {d.priorities.map((p) => (
                      <li key={p.field}><b>{p.label}</b> <span className="dim">{p.level}</span></li>
                    ))}
                  </ul>
                </div>
                {(d.notes.length > 0 || d.changes.length > 0) && (
                  <div>
                    <h3>What you told us</h3>
                    <ul className="brief-list">
                      {d.notes.map((n) => <li key={n}>{n}</li>)}
                      {d.changes.map((c) => <li key={c.field}>{c.label} {c.change}</li>)}
                    </ul>
                  </div>
                )}
              </section>

              <h3>Best-placed districts</h3>
              {d.districts.map((x) => (
                <article key={x.district_id} className="brief-district">
                  <div className="bd-head">
                    <span className="bd-rank">#{x.rank}</span>
                    <span className="bd-name">{x.name}</span>
                    <span className="dim small">{x.region}</span>
                    <span className="bd-score mono">{x.score.toFixed(1)}</span>
                    <span className="dim small">
                      {x.band && (x.band[0] === x.band[1] ? `stable at #${x.band[0]}` : `likely #${x.band[0]}–${x.band[1]}`)}
                      {x.confidence && `, ${x.confidence.toLowerCase()} confidence`}
                    </span>
                  </div>
                  <Paragraph sec={secs[`why_${x.district_id}`]} />
                  <div className="bd-cols">
                    <div>
                      <div className="bd-sub up">Strengths</div>
                      {x.strengths.length ? (
                        <ul>{x.strengths.map((s) => <li key={s.field}>{s.label} <span className="dim">{s.display}</span></li>)}</ul>
                      ) : <p className="dim small">No standout strengths.</p>}
                    </div>
                    <div>
                      <div className="bd-sub down">Weak spots</div>
                      {x.weaknesses.length ? (
                        <ul>{x.weaknesses.map((s) => <li key={s.field}>{s.label} <span className="dim">{s.display}</span></li>)}</ul>
                      ) : <p className="dim small">None that matter here.</p>}
                    </div>
                  </div>
                </article>
              ))}

              <section className="brief-cols">
                <div>
                  <h3>Risks to check</h3>
                  <ul className="brief-list">{d.risks.map((r) => <li key={r}>{r}</li>)}</ul>
                </div>
                <div>
                  <h3>Next steps</h3>
                  <ul className="brief-list steps">{d.next_steps.map((s) => <li key={s}>☐ {s}</li>)}</ul>
                </div>
              </section>

              {d.also_check?.length > 0 && (
                <section className="brief-also" data-testid="brief-also">
                  <h3>Also check before investing</h3>
                  <p className="dim small">Things this industry needs that the district data does not measure (general guidance).</p>
                  <ul className="brief-list steps">
                    {d.also_check.map((n) => (
                      <li key={n.need}>☐ <b>{n.need}</b>: {n.why} <span className="dim">Next: {n.step}</span></li>
                    ))}
                  </ul>
                </section>
              )}

              <section className="brief-sources">
                <h3>Data used</h3>
                <table className="data-table">
                  <tbody>
                    {d.sources.map((s) => (
                      <tr key={s.label}><td>{s.label}</td><td>{s.source}</td><td className={`status s-${s.status.toLowerCase()}`}>{s.status}</td></tr>
                    ))}
                  </tbody>
                </table>
                <p className="dim small">{d.status_note}</p>
                <ul className="dim small brief-limits">{d.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
                <p className="dim tiny">
                  Scores are out of 100 and come from a fixed weighted formula; paragraphs marked with a purple bar were
                  written by a local AI model and checked against these figures. Dataset {d.dataset_hash?.slice(0, 12)}.
                </p>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
