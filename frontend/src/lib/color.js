// Map colors (§12.4): the old project's rank tiers, so the top districts stand out at a glance.

export const TIERS = [
  ['#1', '#38BDF8'],
  ['#2', '#2DD4BF'],
  ['#3', '#22D3EE'],
  ['#4–5', '#1D9FBF'],
  ['#6–10', '#1A6B8A'],
]
export const TIER_REST = '#1B2537'

export function tierColor(rank) {
  if (!rank) return TIER_REST
  if (rank === 1) return TIERS[0][1]
  if (rank === 2) return TIERS[1][1]
  if (rank === 3) return TIERS[2][1]
  if (rank <= 5) return TIERS[3][1]
  if (rank <= 10) return TIERS[4][1]
  return TIER_REST
}

// Map each eligible district id to its tier color.
export function tierFills(ranking) {
  const out = {}
  for (const e of ranking || []) if (e.eligible) out[e.district_id] = tierColor(e.rank)
  return out
}
