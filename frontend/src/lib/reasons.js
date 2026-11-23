// Short wording for why a district is not eligible. The server joins its reasons with "; ", e.g.
// "no industrial land; crop output below the minimum (10 k t)" -> ["no industrial land", "low crop output"].

function short(reason, withValue) {
  const min = reason.match(/^(.*) below the minimum \((.*)\)$/)
  if (min) return withValue ? `low ${min[1]} (${min[2]})` : `low ${min[1]}`
  const port = reason.match(/^port (\d+) km > \d+ km$/)
  if (port) return withValue ? `too far from a port (${port[1]} km)` : 'too far from a port'
  return reason
}

// One district's reasons. `withValue` keeps the district's own number, for its popup.
export function reasonList(reason, withValue = false) {
  return String(reason || '')
    .split(/;\s*/)
    .filter(Boolean)
    .map((r) => short(r, withValue))
}

// All excluded districts' reasons, most common first, without duplicates.
export function excludedCauses(excluded) {
  const n = {}
  for (const e of excluded || []) for (const r of new Set(reasonList(e.reason))) n[r] = (n[r] || 0) + 1
  return Object.keys(n).sort((a, b) => n[b] - n[a])
}
