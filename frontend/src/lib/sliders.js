// Visual sliders (§5.5). Position is sqrt(weight) so small but real weights (5–15%) sit
// visibly along the track instead of bunching at the left edge. No number is ever shown.
export const toPos = (w) => Math.sqrt(Math.max(0, Math.min(1, w || 0)))
export const toWeight = (p) => Math.max(0, Math.min(1, p)) ** 2

// Preview of the rebalance the server will do (backend/modifiers.apply) while a slider is
// dragged: earlier slider overrides stay fixed, other non-zero factors scale proportionally,
// and if every other factor is at 0 the remainder is split evenly across them.
export function rebalance(weights, overrides, field, value) {
  const fixed = { ...overrides, [field]: value }
  const fixedSum = Object.values(fixed).reduce((a, b) => a + b, 0)
  const free = Object.keys(weights).filter((k) => !(k in fixed))
  const freeSum = free.reduce((a, k) => a + weights[k], 0)
  const out = {}
  if (freeSum <= 0 && free.length && fixedSum < 1) {
    for (const k of free) out[k] = (1 - fixedSum) / free.length
    return { ...out, ...fixed }
  }
  if (fixedSum >= 1 || freeSum <= 0) {
    const total = fixedSum + freeSum
    for (const k of free) out[k] = weights[k] / total
    for (const k in fixed) out[k] = fixed[k] / total
    return out
  }
  for (const k of free) out[k] = (weights[k] * (1 - fixedSum)) / freeSum
  return { ...out, ...fixed }
}

// Words, never numbers, for screen readers.
export function importanceWords(w, max) {
  if (w <= 0.001) return "doesn't matter"
  const r = max > 0 ? w / max : 0
  if (r > 0.85) return 'matters most'
  if (r > 0.5) return 'matters a lot'
  if (r > 0.25) return 'matters somewhat'
  return 'matters a little'
}
