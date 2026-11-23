import { useStore } from '../store.js'
import { TIERS } from '../lib/color.js'
import { excludedCauses } from '../lib/reasons.js'

// Old-style rank legend plus the "not eligible" hatch line, and the Clusters toggle under it.
// The not-eligible line stays short: a count and the main causes; hovering a district gives its own reason.
export default function Legend() {
  const state = useStore((s) => s.state)
  const clusterMode = useStore((s) => s.ui.clusterMode)
  const toggleClusters = useStore((s) => s.toggleClusters)
  const ready = useStore((s) => !!s.meta)
  if (!ready) return null

  const ex = state?.excluded?.length || 0
  const causes = excludedCauses(state?.excluded)

  return (
    <div className="legend-stack">
      {state?.profile && (
        <div className="legend" aria-label="Legend">
          <div className="overlay-head">Rank</div>
          {TIERS.map(([label, color]) => (
            <div key={label} className="legend-row">
              <span className="legend-swatch" style={{ background: color }} />
              <span>{label}</span>
            </div>
          ))}
          {ex > 0 && (
            <div className="legend-hatch">
              <svg width="14" height="9" aria-hidden="true">
                <rect width="14" height="9" fill="url(#hatch)" />
              </svg>
              <span>
                Not eligible ({ex})
                <span className="legend-why">{causes.slice(0, 3).join(' · ')}</span>
              </span>
            </div>
          )}
        </div>
      )}
      <button className={`cluster-toggle${clusterMode ? ' on' : ''}`} role="switch" aria-checked={clusterMode}
        onClick={toggleClusters}>
        ◈ Clusters {clusterMode ? '▴' : '▾'}
      </button>
    </div>
  )
}
