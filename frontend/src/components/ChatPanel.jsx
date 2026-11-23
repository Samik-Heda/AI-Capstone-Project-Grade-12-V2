import { memo, useEffect, useRef } from 'react'
import { useStore } from '../store.js'
import { Markdown } from '../lib/markdown.jsx'
import { reasonList } from '../lib/reasons.js'

// §12.3 example chips; the last one opens the profile builder instead of sending a message.
const BUILD_CHIP = 'Build a custom profile'
const EXAMPLES = [
  'Food processing plant, water is critical',
  'Automobile hub',
  'Compare Pune and Nashik',
  'Textile mill, ignore crop output',
  'Cheapest land for logistics',
  BUILD_CHIP,
]

// Weight changes in words, never as percentages (§5.5); same wording as backend/templates.py.
const CHANGE = {
  critical: { mark: '↑↑', text: 'now matters most', cls: 'up' },
  high: { mark: '↑', text: 'now matters more', cls: 'up' },
  low: { mark: '↓', text: 'now matters less', cls: 'down' },
  exclude: { mark: '✕', text: 'is now ignored', cls: 'down' },
}
const CARD_TITLE = {
  rank: (p) => `Top districts for ${p}`,
  apply_preset: (p) => `Top districts for ${p}`,
  clarify_answer: (p) => `Top districts for ${p}`,
  current_preset: (p) => `Already showing ${p}`,
  adjust: () => 'Priorities updated',
}

const EMPTY = []

function usePlain() {
  const fields = useStore((s) => s.meta?.fields || EMPTY)
  return Object.fromEntries(fields.map((f) => [f.key, f.plain_label]))
}

// Ranking-type replies as a small card: headline, what changed, the top 3, and who leads.
function RankCard({ m }) {
  const plain = usePlain()
  const { top = [], changes = {} } = m.facts
  const ctx = m.ctx || {}
  const title = (CARD_TITLE[m.kind] || CARD_TITLE.rank)(ctx.profile || 'this industry')
  const lead = top[0]
  const unparsed = m.understood?.unparsed_fields || []
  return (
    <div className="reply-card">
      <div className="card-title">{title}</div>
      {Object.keys(changes).length > 0 && (
        <ul className="change-list">
          {Object.entries(changes).map(([f, lab]) => (
            <li key={f} className={CHANGE[lab]?.cls}>
              <span className="mark">{CHANGE[lab]?.mark}</span> <b>{plain[f]}</b> {CHANGE[lab]?.text}
            </li>
          ))}
        </ul>
      )}
      {top.length > 0 ? (
        <ol className="mini-top">
          {top.map((e) => (
            <li key={e.district_id}>
              <span className="mt-rank">{e.rank}</span>
              <span className="mt-name">{e.name}</span>
              <span className="mt-score">{Math.round(e.score)}</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted small">No district is eligible under the current constraints.</p>
      )}
      {m.kind === 'adjust' && lead && (
        <p className="lead-line">
          {ctx.prevLeader && ctx.prevLeader !== lead.name ? (
            <><b>{lead.name}</b> now leads <span className="dim">(was {ctx.prevLeader})</span></>
          ) : (
            <><b>{lead.name}</b> still leads</>
          )}
        </p>
      )}
      {unparsed.length > 0 && (
        <p className="muted small">
          You mentioned {unparsed.map((f) => plain[f]?.toLowerCase()).join(' and ')} without saying how much it matters.
        </p>
      )}
      {m.facts.tip && <Tip t={m.facts.tip} plain={plain} />}
      {ctx.excluded > 0 && (
        <p className="card-foot">
          {ctx.excluded} district{ctx.excluded === 1 ? '' : 's'} excluded ({ctx.excludedReason})
        </p>
      )}
    </div>
  )
}

const ordinal = (n) => {
  const suf = n % 100 >= 10 && n % 100 <= 20 ? 'th' : { 1: 'st', 2: 'nd', 3: 'rd' }[n % 10] || 'th'
  return `${n}${suf}`
}

// §18.2 trade-off tip: the leader is weak on something this industry cares about.
function Tip({ t, plain }) {
  return (
    <p className="tip-line" data-testid="tip">
      <span className="mark">⚠</span>Heads up: <b>{t.name}</b> ranks {ordinal(t.rank)} of {t.of} on{' '}
      {plain[t.field]?.toLowerCase()}, one of the things that matters most for this industry.
    </p>
  )
}

// §18.2 the advisor's open question; the user answers by typing.
function AdvisorQuestion({ q }) {
  return (
    <div className="advisor-q" data-testid="advisor-question">
      <span className="q-head">Quick question</span>
      {q.text}
    </div>
  )
}

const CONF_CLASS = { High: 'c-high', Medium: 'c-med', Low: 'c-low' }

// "Explain <district>" as a card: where it stands, then strengths and weaknesses as two separate
// lists, then the gap to #1 (or the lead over #2). Built from the reply's facts, not its text.
function ExplainCard({ m }) {
  const plain = usePlain()
  const { entry: e, strengths = [], weaknesses = [], why_not_first: wnf, focus = [], note } = m.facts
  const ctx = m.ctx || {}
  const behind = (wnf?.behind || []).map((g) => plain[g.field]?.toLowerCase()).filter(Boolean)
  return (
    <div className="reply-card explain-card">
      <div className="card-title">{e.name}</div>
      {e.eligible ? (
        <div className="ex-rank">
          <span className="ex-num">#{e.rank}</span>
          <span className="muted">of {ctx.eligible || '?'} for {ctx.profile}</span>
          <span className="ex-score mono">{e.score.toFixed(1)}</span>
          <span className={`conf-chip ${CONF_CLASS[e.confidence]}`}>{e.confidence}</span>
        </div>
      ) : (
        <div className="ex-inelig">
          <b>Not ranked</b> for {ctx.profile}: {reasonList(e.reason, true).join(', ')}
        </div>
      )}
      {focus.length > 0 && (
        <ul className="ex-focus">
          {focus.map((f) => (
            <li key={f.field}>
              <b>{plain[f.field]}</b> {f.display} <span className="dim">· #{f.rank} of {f.of} on this</span>
            </li>
          ))}
        </ul>
      )}
      <div className="ex-box up">
        <div className="ex-head">+ Strengths</div>
        {strengths.length ? (
          <ul>
            {strengths.map((x) => (
              <li key={x.field}><span>{plain[x.field]}</span><span className="ex-val">{x.display}</span></li>
            ))}
          </ul>
        ) : (
          <p className="muted small">No standout strengths for this industry.</p>
        )}
      </div>
      <div className="ex-box down">
        <div className="ex-head">− Weaknesses</div>
        {weaknesses.length ? (
          <ul>
            {weaknesses.map((x) => (
              <li key={x.field}><span>{plain[x.field]}</span><span className="ex-val">{x.display}</span></li>
            ))}
          </ul>
        ) : (
          <p className="muted small">None that matter for this industry.</p>
        )}
      </div>
      {e.eligible && e.rank === 1 && ctx.runnerUp && (
        <p className="lead-line">
          Leads #2 {ctx.runnerUp.name} by <b>{(e.score - ctx.runnerUp.score).toFixed(1)} pts</b>
        </p>
      )}
      {e.eligible && e.rank !== 1 && wnf?.first && (
        <p className="lead-line">
          <b>{wnf.total_gap.toFixed(1)} pts</b> behind #1 {wnf.first.name}
          {behind.length > 0 && <span className="muted"> · mostly {behind.join(', ')}</span>}
        </p>
      )}
      {note && <p className="card-foot">{note}</p>}
    </div>
  )
}

// Only a flagged note is marked; a grounded one needs no badge.
function Grounding({ g }) {
  if (!g || g.grounded) return null
  const bad = [...(g.unsupported_numbers || []), ...(g.wrong_directions || [])].join(', ')
  return <span className="badge warn" title={bad || 'unsupported claim'}>⚠ check numbers</span>
}

// §19.1 answers may end with "General advice:" (the model's own knowledge, no numbers): shown apart, to verify.
const ADVICE_RE = /\s*\**General advice:?\**:?\s*/i

function Narration({ n, title = 'AI note', testid }) {
  if (!n) return null
  if (n.status === 'aborted' && !n.text) return null
  const cut = n.text ? n.text.search(ADVICE_RE) : -1
  const main = cut >= 0 ? n.text.slice(0, cut).trim() : n.text
  const advice = cut >= 0 ? n.text.slice(cut).replace(ADVICE_RE, '').trim() : ''
  const streaming = n.status === 'streaming'
  return (
    <div className="ai-note" aria-live="polite" data-status={n.status} data-testid={testid}
      data-grounded={n.status === 'done' ? String(!!n.grounding?.grounded) : undefined}>
      <div className="ai-head">
        <span>{title}</span>
        {streaming && !n.text && <span className="shimmer dim">writing…</span>}
        {n.status === 'done' && <Grounding g={n.grounding} />}
      </div>
      {main && (
        <div className="ai-text">
          <Markdown text={main} />
          {streaming && !advice && <span className="cursor-blink" aria-hidden="true">▊</span>}
        </div>
      )}
      {advice && (
        <div className="ai-advice" data-testid="general-advice">
          <div className="ai-advice-head">General advice — please verify</div>
          <Markdown text={advice} />
          {streaming && <span className="cursor-blink" aria-hidden="true">▊</span>}
        </div>
      )}
      {n.status === 'error' && (
        // server errors already read "AI note unavailable (timeout)"; client ones are a bare reason
        <p className="muted small">AI note unavailable ({n.error?.match(/^AI note unavailable \((.*)\)$/)?.[1] || n.error || 'error'}).</p>
      )}
    </div>
  )
}

function CompareTable({ cmp }) {
  const plain = usePlain()
  const rows = [...cmp.rows].sort((x, y) => y.weight - x.weight)
  const tag = (x) => (x.eligible ? `#${x.rank}` : 'not ranked')
  return (
    <table className="compare-table" data-testid="compare-table">
      <thead>
        <tr>
          <th />
          <th className={cmp.direction === 'a' ? 'lead' : ''}>{cmp.a.name}</th>
          <th className={cmp.direction === 'b' ? 'lead' : ''}>{cmp.b.name}</th>
        </tr>
        <tr className="score-row">
          <td>Score</td>
          <td className={cmp.direction === 'a' ? 'lead' : ''}>{cmp.a.score.toFixed(1)} <span className="muted">{tag(cmp.a)}</span></td>
          <td className={cmp.direction === 'b' ? 'lead' : ''}>{cmp.b.score.toFixed(1)} <span className="muted">{tag(cmp.b)}</span></td>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.field} className={r.weight < 0.05 ? 'minor' : ''}>
            <td>{plain[r.field]}</td>
            <td className={r.leader === 'a' ? 'lead' : ''}>{r.a.display}</td>
            <td className={r.leader === 'b' ? 'lead' : ''}>{r.b.display}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// Memoized: a new reply or map update never re-renders the older messages.
const Message = memo(function Message({ m, last }) {
  const send = useStore((s) => s.send)
  const busy = useStore((s) => s.ui.busy)
  if (m.role === 'user') return <div className="msg user">{m.md}</div>
  const card = m.facts?.top && CARD_TITLE[m.kind]
  return (
    <div className={`msg bot${m.kind === 'error' ? ' error' : ''}`} data-kind={m.kind}>
      {m.kind === 'explain_district' && m.facts?.entry ? <ExplainCard m={m} /> : card ? <RankCard m={m} /> : <Markdown text={m.md} />}
      {m.kind === 'compare' && m.facts?.compare && <CompareTable cmp={m.facts.compare} />}
      <Narration n={m.narration} title={m.kind === 'ask' || m.kind === 'chat' ? 'AI answer' : 'AI note'} />
      <Narration n={m.detail} title="Detailed answer (advanced model)" testid="detail-answer" />
      {m.viaAI && <p className="ai-read">Understood with AI reading. Nothing changes until you apply it.</p>}
      {m.interpreting && (
        <div className="ai-note" data-testid="interpreting">
          <div className="ai-head"><span>AI</span><span className="shimmer dim">thinking it over…</span></div>
        </div>
      )}
      {m.question && <AdvisorQuestion q={m.question} />}
      {m.chips?.length > 0 && (
        <div className="chips">
          {m.chips.map((c, i) => (
            <button key={i} className="chip" disabled={busy || !last} onClick={() => send(c.value)}>
              {c.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
})

export default function ChatPanel() {
  const openBuilder = useStore((s) => s.openBuilder)
  const messages = useStore((s) => s.messages)
  const send = useStore((s) => s.send)
  const busy = useStore((s) => s.ui.busy)
  const ready = useStore((s) => !!s.sessionId)
  const draft = useStore((s) => s.draft)
  const setDraft = useStore((s) => s.setDraft)
  const asking = useStore((s) => !!s.state?.pending_question)
  const listRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  useEffect(() => {
    if (draft) inputRef.current?.focus()
  }, [draft])

  const submit = (e) => {
    e.preventDefault()
    if (!draft.trim() || busy) return
    send(draft)
    setDraft('')
  }

  return (
    <section className="chat-panel" aria-label="Chat">
      <div className="messages" ref={listRef}>
        {messages.length === 0 ? (
          <div className="chat-empty">
            <h2>What do you want to build?</h2>
            <p className="muted">Describe your business in your own words, or try one of these:</p>
            <div className="chips">
              {EXAMPLES.map((t) => (
                <button
                  key={t}
                  className={`chip${t === BUILD_CHIP ? ' chip-build' : ''}`}
                  disabled={!ready || busy}
                  onClick={() => (t === BUILD_CHIP ? openBuilder() : send(t))}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m, i) => <Message key={m.id} m={m} last={i === messages.length - 1} />)
        )}
        {busy && <div className="msg bot typing"><span className="shimmer">Analyzing…</span></div>}
      </div>
      <form className="chat-input" onSubmit={submit}>
        <textarea
          ref={inputRef}
          rows={2}
          value={draft}
          placeholder={asking ? 'Type your answer in your own words…' : 'Describe an industry, ask about a district, or compare two…'}
          aria-label="Message"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) submit(e)
          }}
        />
        <button type="submit" className="send-btn" disabled={!ready || busy || !draft.trim()} aria-label="Send">
          Send →
        </button>
      </form>
    </section>
  )
}
