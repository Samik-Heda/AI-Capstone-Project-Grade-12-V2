import { useState, useEffect, useCallback } from "react";

const API = "/api";

const CLUSTER_COLORS = ["#A78BFA", "#FBBF24", "#2DD4BF", "#FB7185"];

export default function ClusterOverlay({ weightVector, visible, onToggle, onClustersChange }) {
  const [clusters, setClusters] = useState(null);
  const [loading, setLoading] = useState(false);

  const fetchClusters = useCallback(async () => {
    if (!weightVector || !visible) return;
    setLoading(true);
    try {
      const res = await fetch(`${API}/cluster`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ weight_vector: weightVector, k: 4 }),
      });
      const data = await res.json();
      setClusters(data);
      onClustersChange && onClustersChange(data.district_cluster_map || {});
    } catch {
      setClusters(null);
    } finally {
      setLoading(false);
    }
  }, [weightVector, visible]);

  useEffect(() => {
    if (visible && weightVector) fetchClusters();
    if (!visible) onClustersChange && onClustersChange({});
  }, [visible, weightVector]);

  return (
    <>
      {/* Toggle button — bottom-left of map */}
      <button
        onClick={onToggle}
        style={{
          position: "absolute", bottom: "16px", left: "16px", zIndex: 5,
          background: visible ? "var(--bg-overlay)" : "rgba(30,45,66,0.85)",
          border: `1px solid ${visible ? "var(--accent-purple)" : "var(--border-subtle)"}`,
          color: visible ? "var(--accent-purple)" : "var(--text-muted)",
          fontSize: "11px", padding: "5px 10px", borderRadius: "6px",
          cursor: "pointer", fontFamily: "inherit", backdropFilter: "blur(6px)",
          transition: "all 0.2s",
        }}
      >
        ◈ Clusters {visible ? "▴" : "▾"}
      </button>

      {/* Cluster panel — floats over map, top-left */}
      {visible && (
        <div style={{
          position: "absolute", top: "16px", left: "16px", zIndex: 4,
          width: "220px",
          background: "rgba(26,37,53,0.95)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "10px", overflow: "hidden",
          boxShadow: "0 4px 24px rgba(0,0,0,0.5)", backdropFilter: "blur(8px)",
        }}>
          <div style={{
            padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)",
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <span style={{ fontSize: "10px", fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-muted)" }}>
              District Clusters
            </span>
            {loading && <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>updating...</span>}
          </div>

          {!clusters && !loading && (
            <div style={{ padding: "12px", fontSize: "11px", color: "var(--text-muted)" }}>
              No cluster data yet.
            </div>
          )}

          {clusters && (
            <div style={{ padding: "8px" }}>
              {clusters.clusters.map((cluster) => (
                <div
                  key={cluster.id}
                  style={{
                    marginBottom: "6px", borderRadius: "6px",
                    borderLeft: `3px solid ${cluster.color}`,
                    background: "var(--bg-elevated)",
                    padding: "7px 8px",
                  }}
                >
                  <div style={{ fontSize: "11px", fontWeight: 600, color: "var(--text-primary)", marginBottom: "3px" }}>
                    {cluster.label}
                  </div>
                  <div style={{ fontSize: "10px", color: "var(--text-muted)", lineHeight: "1.4" }}>
                    {cluster.districts.slice(0, 6).join(", ")}
                    {cluster.districts.length > 6 && ` +${cluster.districts.length - 6} more`}
                  </div>
                  <div style={{ display: "flex", gap: "4px", marginTop: "4px", flexWrap: "wrap" }}>
                    {cluster.dominant_features.map(f => (
                      <span key={f} style={{
                        fontSize: "9px", padding: "1px 5px", borderRadius: "3px",
                        background: `${cluster.color}22`, color: cluster.color,
                        border: `1px solid ${cluster.color}44`,
                      }}>
                        {f}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
              <div style={{ fontSize: "9px", color: "var(--text-muted)", padding: "4px 4px 0", textAlign: "center" }}>
                Updates as weights change
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
