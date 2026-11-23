// Safe markdown: builds React elements (never HTML strings), so nothing in a reply can inject
// markup. Supports only what the templates and narration use: **bold**, bullet and numbered
// lists, pipe tables and paragraphs.

function inline(text, key) {
  const out = []
  const re = /\*\*(.+?)\*\*/g
  let last = 0
  let m
  let i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    out.push(<strong key={`${key}-b${i++}`}>{m[1]}</strong>)
    last = re.lastIndex
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

const cells = (line) => line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
const isRule = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line)

export function Markdown({ text, className }) {
  const lines = String(text ?? '').split('\n')
  const blocks = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i++
      continue
    }
    if (line.trim().startsWith('|') && i + 1 < lines.length && isRule(lines[i + 1])) {
      const head = cells(line)
      const rows = []
      i += 2
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(cells(lines[i++]))
      const k = blocks.length
      blocks.push(
        <table key={k} className="md-table">
          <thead>
            <tr>{head.map((c, j) => <th key={j}>{inline(c, `${k}h${j}`)}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri}>{r.map((c, j) => <td key={j}>{inline(c, `${k}r${ri}c${j}`)}</td>)}</tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }
    const bullet = /^\s*[-*•]\s+/
    const numbered = /^\s*\d+[.)]\s+/
    if (bullet.test(line) || numbered.test(line)) {
      const re = bullet.test(line) ? bullet : numbered
      const items = []
      while (i < lines.length && re.test(lines[i])) items.push(lines[i++].replace(re, ''))
      const k = blocks.length
      const List = re === bullet ? 'ul' : 'ol'
      blocks.push(<List key={k}>{items.map((t, j) => <li key={j}>{inline(t, `${k}i${j}`)}</li>)}</List>)
      continue
    }
    const para = []
    while (i < lines.length && lines[i].trim() && !lines[i].trim().startsWith('|') && !bullet.test(lines[i]) && !numbered.test(lines[i])) {
      para.push(lines[i++])
    }
    const k = blocks.length
    blocks.push(<p key={k}>{inline(para.join(' '), `${k}p`)}</p>)
  }
  return <div className={className ? `md ${className}` : 'md'}>{blocks}</div>
}
