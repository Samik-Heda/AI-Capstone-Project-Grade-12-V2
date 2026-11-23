import { useState, useRef, useEffect, useCallback } from "react";
import { DISTRICT_PATHS, VIEWBOX, getDistrictColor } from "../maharashtraMap.js";
import RankingsOverlay from "./RankingsOverlay.jsx";
import WeightsOverlay from "./WeightsOverlay.jsx";
import ClusterOverlay from "./ClusterOverlay.jsx";

// Short labels that fit inside district polygons
const SHORT_NAMES = {
  "Mumbai City": "MUM", "Mumbai Suburban": "M.Sub", "Thane": "Thane",
  "Raigad": "Raigad", "Ratnagiri": "Ratn.", "Sindhudurg": "Sindh.",
  "Palghar": "Palgh.", "Nashik": "Nashik", "Ahmednagar": "Ahmd.",
  "Dhule": "Dhule", "Nandurbar": "Nand.", "Jalgaon": "Jalg.",
  "Pune": "Pune", "Satara": "Satara", "Sangli": "Sangli",
  "Solapur": "Solap.", "Kolhapur": "Kolh.", "Aurangabad": "Aurang.",
  "Jalna": "Jalna", "Beed": "Beed", "Latur": "Latur",
  "Osmanabad": "Osm.", "Nanded": "Nanded", "Hingoli": "Hing.",
  "Parbhani": "Parb.", "Nagpur": "Nagpur", "Wardha": "Wardha",
  "Yavatmal": "Yavat.", "Amravati": "Amrav.", "Akola": "Akola",
  "Washim": "Wash.", "Buldhana": "Buld.", "Chandrapur": "Chand.",
  "Gadchiroli": "Gadc.", "Gondia": "Gondia", "Bhandara": "Bhand.",
};

const CLUSTER_COLORS = ["#A78BFA", "#FBBF24", "#2DD4BF", "#FB7185"];

export default function MapPanel({
  rankings, loading, resolvedIndustry, weightVector,
  weightModifiers, onDistrictClick, onResetWeights, learnedFields,
}) {
  const [tooltip, setTooltip] = useState(null);
  const [hoveredId, setHoveredId] = useState(null);
  const [showClusters, setShowClusters] = useState(false);
  const [clusterMap, setClusterMap] = useState({});

  // Pan mode state
  const [panMode, setPanMode] = useState(false);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const panRef = useRef({ active: false, startX: 0, startY: 0, originX: 0, originY: 0 });

  const togglePan = useCallback(() => {
    setPanMode(m => !m);
  }, []);

  useEffect(() => {
    if (!panMode) return;
    const onMove = (e) => {
      if (!panRef.current.active) return;
      const dx = e.clientX - panRef.current.startX;
      const dy = e.clientY - panRef.current.startY;
      setPan({ x: panRef.current.originX + dx, y: panRef.current.originY + dy });
    };
    const onUp = () => { panRef.current.active = false; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, [panMode]);

  const handleMouseDown = (e) => {
    if (!panMode) return;
    e.preventDefault();
    panRef.current = { active: true, startX: e.clientX, startY: e.clientY, originX: pan.x, originY: pan.y };
  };

  const rankMap = {};
  const scoreMap = {};
  for (const r of rankings) {
    rankMap[r.district_id] = r.rank;
    scoreMap[r.district_id] = r.score;
  }

  return (
    <div style={{ flex: 1, position: "relative", background: "var(--bg-base)", overflow: "hidden" }}>

      {/* Pan hint */}
      {!panMode && (
        <div style={{
          position: "absolute", bottom: "16px", right: "16px", zIndex: 6,
          fontSize: "10px", color: "var(--text-muted)", pointerEvents: "none",
        }}>
          [double-click to pan]
        </div>
      )}

      <svg
        viewBox={VIEWBOX}
        preserveAspectRatio="xMidYMid meet"
        style={{
          position: "absolute", top: 0, left: 0, width: "100%", height: "100%",
          display: "block",
          cursor: panMode ? (panRef.current.active ? "grabbing" : "grab") : "default",
          transform: `translate(${pan.x}px, ${pan.y}px)`,
          transition: panRef.current.active ? "none" : "transform 0.1s ease",
        }}
        onDoubleClick={togglePan}
        onMouseDown={handleMouseDown}
        onMouseLeave={() => { if (!panMode) { setTooltip(null); setHoveredId(null); } }}
      >
        {/* Region labels */}
        <text x="196" y="700" textAnchor="middle" fill="#2E4060" fontSize="9" fontFamily="Inter,sans-serif" fontWeight="500">KONKAN</text>
        <text x="290" y="220" textAnchor="middle" fill="#2E4060" fontSize="9" fontFamily="Inter,sans-serif" fontWeight="500">N. MAHARASHTRA</text>
        <text x="294" y="590" textAnchor="middle" fill="#2E4060" fontSize="9" fontFamily="Inter,sans-serif" fontWeight="500">W. MAHARASHTRA</text>
        <text x="490" y="478" textAnchor="middle" fill="#2E4060" fontSize="9" fontFamily="Inter,sans-serif" fontWeight="500">MARATHWADA</text>
        <text x="640" y="490" textAnchor="middle" fill="#2E4060" fontSize="9" fontFamily="Inter,sans-serif" fontWeight="500">VIDARBHA</text>

        {/* District polygons */}
        {DISTRICT_PATHS.map((dist) => {
          const rank = rankMap[dist.id];
          const score = scoreMap[dist.id];
          const baseFill = loading ? "#1A2535" : getDistrictColor(rank);
          const isHovered = hoveredId === dist.id;
          return (
            <g key={dist.id}>
              <path
                d={dist.d}
                fill={baseFill}
                stroke={showClusters && clusterMap[dist.name] !== undefined ? CLUSTER_COLORS[clusterMap[dist.name] % 4] : "#0B1120"}
                strokeWidth={showClusters && clusterMap[dist.name] !== undefined ? "2.5" : "1.2"}
                opacity={isHovered ? 0.85 : 1}
                style={{
                  cursor: panMode ? "inherit" : "pointer",
                  filter: isHovered && !panMode ? "brightness(1.35)" : "none",
                  transition: "fill 0.4s ease, filter 0.15s ease",
                }}
                onClick={(e) => { if (!panMode) onDistrictClick(dist.id, rank, score); }}
                onMouseEnter={(e) => {
                  if (panMode) return;
                  const parentRect = e.currentTarget.closest("div").getBoundingClientRect();
                  setHoveredId(dist.id);
                  setTooltip({
                    name: dist.name, rank, score,
                    x: e.clientX - parentRect.left,
                    y: e.clientY - parentRect.top,
                  });
                }}
                onMouseMove={(e) => {
                  const parentRect = e.currentTarget.closest("div").getBoundingClientRect();
                  setTooltip((prev) => prev ? { ...prev, x: e.clientX - parentRect.left, y: e.clientY - parentRect.top } : prev);
                }}
                onMouseLeave={() => { setHoveredId(null); setTooltip(null); }}
              />
              {/* District name label */}
              <text
                x={dist.cx}
                y={dist.cy + 4}
                textAnchor="middle"
                fill={rank && rank <= 3 ? "#0B1120" : "#94A3B8"}
                fontSize="7.5"
                fontFamily="Inter,sans-serif"
                fontWeight={rank && rank <= 5 ? "600" : "400"}
                style={{ pointerEvents: "none", userSelect: "none" }}
              >
                {SHORT_NAMES[dist.name] || dist.name}
              </text>
              {/* Rank badge for top 5 */}
              {rank && rank <= 5 && !loading && (
                <text
                  x={dist.cx}
                  y={dist.cy - 6}
                  textAnchor="middle"
                  fill={rank === 1 ? "#0B1120" : "#E2E8F0"}
                  fontSize="8"
                  fontFamily="JetBrains Mono,monospace"
                  fontWeight="700"
                  style={{ pointerEvents: "none" }}
                >
                  #{rank}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {/* Hover Tooltip */}
      {tooltip && (
        <div style={{
          position: "absolute", left: tooltip.x + 14, top: tooltip.y - 12,
          background: "var(--bg-overlay)", border: "1px solid var(--border-active)",
          borderRadius: "6px", padding: "5px 10px", pointerEvents: "none",
          fontSize: "12px", color: "var(--text-primary)", fontWeight: 500,
          boxShadow: "0 4px 16px rgba(0,0,0,0.6)", zIndex: 5, whiteSpace: "nowrap",
        }}>
          {tooltip.name}
          {tooltip.rank && (
            <span style={{ color: "var(--accent-blue)", marginLeft: "8px", fontFamily: "JetBrains Mono, monospace", fontSize: "11px" }}>
              #{tooltip.rank} · {tooltip.score?.toFixed(1)} pts
            </span>
          )}
        </div>
      )}

      <RankingsOverlay rankings={rankings} loading={loading} onDistrictClick={onDistrictClick} />
      <WeightsOverlay
        weightVector={weightVector}
        weightModifiers={weightModifiers}
        resolvedIndustry={resolvedIndustry}
        onReset={onResetWeights}
        learnedFields={learnedFields}
      />
      <ClusterOverlay
        weightVector={weightVector}
        visible={showClusters}
        onToggle={() => setShowClusters(v => !v)}
        onClustersChange={setClusterMap}
      />

      {/* Rank legend */}
      {rankings.length > 0 && (
        <div style={{
          position: "absolute", bottom: "16px", left: "248px",
          background: "rgba(30,45,66,0.92)", border: "1px solid var(--border-subtle)",
          borderRadius: "8px", padding: "8px 12px", backdropFilter: "blur(8px)",
        }}>
          <div style={{ fontSize: "10px", color: "var(--text-muted)", marginBottom: "5px", textTransform: "uppercase", letterSpacing: "0.08em" }}>
            Rank
          </div>
          {[["#1", "#38BDF8"], ["#2", "#2DD4BF"], ["#3", "#22D3EE"], ["#4–5", "#1D9FBF"], ["#6–10", "#1A6B8A"]].map(([label, color]) => (
            <div key={label} style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "2px" }}>
              <div style={{ width: "10px", height: "6px", background: color, borderRadius: "2px" }} />
              <span style={{ fontSize: "10px", color: "var(--text-secondary)" }}>{label}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
