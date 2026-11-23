import { useState } from "react";

const WEIGHT_LABELS = {
  weight_literacy: "Literacy",
  weight_road_density: "Road Density",
  weight_highway_access: "Highway Access",
  weight_rail_connectivity: "Rail Connectivity",
  weight_port_proximity: "Port Proximity",
  weight_power: "Power Supply",
  weight_water: "Water Access",
  weight_crop_output: "Crop Output",
  weight_crop_diversity: "Crop Diversity",
  weight_irrigation: "Irrigation",
  weight_industrial_estates: "Industrial Estates",
  weight_msme: "MSME Ecosystem",
  weight_land_affordability: "Land Affordability",
};

const MODIFIER_TO_WEIGHT = {
  literacy: "weight_literacy",
  road_density: "weight_road_density",
  road: "weight_road_density",
  highway_access: "weight_highway_access",
  rail_connectivity: "weight_rail_connectivity",
  port_proximity: "weight_port_proximity",
  power: "weight_power",
  water_availability: "weight_water",
  crop_output: "weight_crop_output",
  crop_diversity: "weight_crop_diversity",
  irrigation: "weight_irrigation",
  industrial_estates: "weight_industrial_estates",
  msme: "weight_msme",
  land_affordability: "weight_land_affordability",
  land: "weight_land_affordability",
  cost: "weight_land_affordability",
};

export default function WeightsOverlay({ weightVector, weightModifiers, resolvedIndustry, onReset, learnedFields }) {
  const [expanded, setExpanded] = useState(false);

  if (!weightVector) return null;

  const adjustedKeys = new Set(
    Object.keys(weightModifiers || {}).map((k) => MODIFIER_TO_WEIGHT[k]).filter(Boolean)
  );

  const learned = new Set(learnedFields || []);

  const hasLearned = learned.size > 0;

  const sorted = Object.entries(WEIGHT_LABELS)
    .map(([key, label]) => ({ key, label, value: weightVector[key] || 0 }))
    .filter((e) => e.value > 0)
    .sort((a, b) => b.value - a.value);

  return (
    <div style={{
      position: "absolute", bottom: "16px", right: "16px",
      width: "260px", background: "rgba(30,45,66,0.93)",
      border: "1px solid var(--border-subtle)", borderRadius: "10px",
      boxShadow: "0 4px 24px rgba(0,0,0,0.5)", backdropFilter: "blur(8px)",
      overflow: "hidden", zIndex: 4,
    }}>
      <div
        onClick={() => setExpanded((e) => !e)}
        style={{
          padding: "8px 12px", display: "flex", alignItems: "center",
          justifyContent: "space-between", cursor: "pointer",
          borderBottom: expanded ? "1px solid var(--border-subtle)" : "none",
        }}
      >
        <span style={{ fontSize: "11px", fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-muted)" }}>
          ⚖ Weights — {resolvedIndustry?.display_name || ""}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          {hasLearned && (
            <span style={{ fontSize: "9px", padding: "1px 5px", borderRadius: "3px", background: "rgba(45,212,191,0.15)", color: "var(--accent-teal)", border: "1px solid rgba(45,212,191,0.3)", fontWeight: 600 }}>
              AI
            </span>
          )}
          <span style={{ color: "var(--text-muted)", fontSize: "12px" }}>{expanded ? "▴ hide" : "▾ show"}</span>
        </div>
      </div>

      {expanded && (
        <div style={{ padding: "8px 12px 10px" }}>
          {sorted.map(({ key, label, value }) => {
            const pct = Math.round(value * 100);
            const isAdjusted = adjustedKeys.has(key);
            const isLearned = learned.has(key);
            const barColor = isLearned ? "var(--accent-teal)" : isAdjusted ? "var(--accent-amber)" : "var(--accent-blue)";
            const textColor = isLearned ? "var(--accent-teal)" : isAdjusted ? "var(--accent-amber)" : "var(--text-muted)";
            return (
              <div key={key} className={isAdjusted && !isLearned ? "pulse-amber" : ""} style={{ marginBottom: "6px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "2px", alignItems: "center" }}>
                  <span style={{ fontSize: "11px", color: "var(--text-secondary)" }}>{label}</span>
                  <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                    {isLearned && (
                      <span style={{ fontSize: "9px", color: "var(--accent-teal)", fontWeight: 600 }}>▴ AI</span>
                    )}
                    <span style={{ fontSize: "11px", fontFamily: "JetBrains Mono, monospace", color: textColor }}>
                      {pct}%{isAdjusted && !isLearned ? " ↑" : ""}
                    </span>
                  </div>
                </div>
                <div style={{ height: "4px", background: "var(--border-subtle)", borderRadius: "2px", overflow: "hidden" }}>
                  <div style={{ width: `${pct}%`, height: "100%", background: barColor, borderRadius: "2px" }} />
                </div>
              </div>
            );
          })}
          <div style={{ borderTop: "1px solid var(--border-subtle)", marginTop: "8px", paddingTop: "8px" }}>
            {hasLearned && (
              <div style={{ fontSize: "10px", color: "var(--accent-teal)", marginBottom: "6px", fontStyle: "italic" }}>
                ▴ AI-adapted from session signals
              </div>
            )}
            <button onClick={onReset} style={{
              width: "100%", background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)",
              borderRadius: "4px", padding: "4px 8px", color: "var(--text-secondary)",
              fontSize: "11px", cursor: "pointer", fontFamily: "inherit",
            }}>
              Reset to defaults
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
