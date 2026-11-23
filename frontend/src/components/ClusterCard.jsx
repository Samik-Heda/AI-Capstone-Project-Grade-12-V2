import { useMemo } from 'react'
import { useStore } from '../store.js'

// §10 district types, old-style cards: on the map each district gets an outline in its cluster's color.
export default function ClusterCard() {
  const clusters = useStore((s) => s.ui.clusters)
  const districts = useStore((s) => s.meta?.districts)
  const names = useMemo(() => Object.fromEntries((districts || []).map((d) => [d.id, d.name])), [districts])

  return (
    <div className="cluster-card" aria-label="District clusters">
      <div className="overlay-head">
        <span>District clusters</span>
        {!clusters && <span className="dim shimmer">updating…</span>}
      </div>
      {clusters && (
        <ul className="cluster-list">
          {clusters.clusters.map((c) => {
            const ds = c.district_ids.map((id) => names[id])
            const traits = c.label.slice(c.name.length + 2).split(/,\s*|;\s*/).filter(Boolean)
            return (
              <li key={c.id} data-cluster={c.id} style={{ borderLeftColor: c.color }}>
                <div className="cluster-name">{c.name} <span className="dim">({ds.length})</span></div>
                <div className="cluster-districts">
                  {ds.slice(0, 6).join(', ')}
                  {ds.length > 6 && ` +${ds.length - 6} more`}
                </div>
                {traits.length > 0 && (
                  <div className="cluster-tags">
                    {traits.map((t) => (
                      <span key={t} style={{ color: c.color, background: `${c.color}22`, borderColor: `${c.color}44` }}>{t}</span>
                    ))}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
      <div className="dim tiny cluster-foot">
        {clusters && <div className="mono">k = {clusters.k} · silhouette {clusters.silhouette.toFixed(2)}</div>}
        Grouped by all 13 factors, unweighted. Fill still shows the rank.
      </div>
    </div>
  )
}
