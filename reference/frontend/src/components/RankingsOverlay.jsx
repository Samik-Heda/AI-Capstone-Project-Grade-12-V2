import { getDistrictColor } from "../maharashtraMap.js";

export default function RankingsOverlay({ rankings, loading, onDistrictClick }) {
  if (!rankings.length && !loading) return null;

  const maxScore = rankings[0]?.score || 100;

  return (
    <div style={{
      position: "absolute", top: "16px", left: "16px",
      width: "220px", background: "rgba(30,45,66,0.93)",
      border: "1px solid var(--border-subtle)", borderRadius: "10px",
      boxShadow: "0 4px 24px rgba(0,0,0,0.5)", backdropFilter: "blur(8px)",
      overflow: "hidden", zIndex: 4,
    }}>
      <div style={{
        padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)",
        fontSize: "11px", fontWeight: 500, textTransform: "uppercase",
        letterSpacing: "0.08em", color: "var(--text-muted)",
      }}>
        Rankings
      </div>

      {loading ? (
        Array.from({ length: 10 }).map((_, i) => (
          <div key={i} className="shimmer" style={{
            height: "32px", margin: "4px 12px", background: "var(--bg-elevated)",
            borderRadius: "4px",
          }} />
        ))
      ) : (
        <div>
          {rankings.slice(0, 10).map((r, i) => {
            const barWidth = Math.round((r.score / maxScore) * 100);
            const color = getDistrictColor(r.rank);
            const isDivider = i === 4;
            return (
              <div key={r.district_id}>
                {isDivider && <div style={{ height: "1px", background: "var(--border-subtle)", margin: "0 12px" }} />}
                <div
                  onClick={() => onDistrictClick(r.district_id, r.rank, r.score)}
                  style={{
                    display: "flex", alignItems: "center", gap: "8px",
                    padding: "0 12px", height: "32px", cursor: "pointer",
                    transition: "background 0.15s",
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.background = "var(--bg-elevated)"}
                  onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                >
                  <span style={{ width: "20px", color: "var(--text-muted)", fontSize: "10px", fontFamily: "JetBrains Mono, monospace", flexShrink: 0 }}>
                    #{r.rank}
                  </span>
                  <span style={{ flex: 1, fontSize: "12px", color: "var(--text-primary)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {r.district_name}
                  </span>
                  <div style={{ width: "50px", height: "4px", background: "var(--border-subtle)", borderRadius: "2px", overflow: "hidden", flexShrink: 0 }}>
                    <div style={{ width: `${barWidth}%`, height: "100%", background: color, borderRadius: "2px" }} />
                  </div>
                  <span style={{ width: "32px", textAlign: "right", fontSize: "11px", color: "var(--text-secondary)", fontFamily: "JetBrains Mono, monospace", flexShrink: 0 }}>
                    {r.score.toFixed(0)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
