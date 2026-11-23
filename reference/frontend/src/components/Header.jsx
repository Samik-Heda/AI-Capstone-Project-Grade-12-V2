import { useState, useRef, useEffect } from "react";

export default function Header({
  resolvedIndustry, weightModifiers, loading, onSourcesClick, rankings,
  allProfiles, activeProfileName, onProfileSelect, onCreateProfile,
}) {
  const hasModifiers = Object.keys(weightModifiers || {}).length > 0;
  const avgCompleteness = rankings.length
    ? Math.round(rankings.reduce((s, r) => s + (r.data_completeness_pct || 100), 0) / rankings.length)
    : 89;

  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (!dropdownRef.current?.contains(e.target)) setDropdownOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const standard = allProfiles?.standard || [];
  const generated = allProfiles?.generated || [];
  const custom = allProfiles?.custom || [];

  const displayName = activeProfileName || resolvedIndustry?.display_name || null;

  return (
    <header style={{
      height: "56px", background: "var(--bg-surface)",
      borderBottom: "1px solid var(--border-subtle)",
      display: "flex", alignItems: "center", padding: "0 16px", gap: "16px",
      flexShrink: 0, zIndex: 10,
    }}>
      {/* Logo */}
      <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: "180px" }}>
        <span style={{ fontSize: "18px" }}>◈</span>
        <span style={{ fontSize: "15px", fontWeight: 600, color: "var(--text-primary)" }}>GrowthCompass</span>
      </div>

      {/* Profile selector dropdown */}
      <div ref={dropdownRef} style={{ position: "relative", flexShrink: 0 }}>
        <button
          onClick={() => setDropdownOpen(o => !o)}
          style={{
            background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)",
            color: displayName ? "var(--text-primary)" : "var(--text-muted)",
            fontSize: "12px", padding: "5px 10px", borderRadius: "6px",
            cursor: "pointer", fontFamily: "inherit", display: "flex", alignItems: "center", gap: "6px",
            maxWidth: "200px",
          }}
        >
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {displayName || "Select Profile"}
          </span>
          <span style={{ color: "var(--text-muted)", flexShrink: 0 }}>▾</span>
        </button>

        {dropdownOpen && (
          <div style={{
            position: "absolute", top: "calc(100% + 4px)", left: 0,
            background: "var(--bg-overlay)", border: "1px solid var(--border-subtle)",
            borderRadius: "8px", minWidth: "220px", maxHeight: "360px", overflowY: "auto",
            boxShadow: "0 8px 32px rgba(0,0,0,0.6)", zIndex: 20,
          }}>
            {/* Standard profiles */}
            <div style={{ padding: "6px 10px 2px", fontSize: "10px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
              Standard
            </div>
            {standard.map(p => (
              <ProfileOption
                key={p.industry_key}
                name={p.display_name}
                active={activeProfileName === p.display_name}
                onClick={() => { onProfileSelect({ type: "standard", key: p.industry_key, name: p.display_name, weights: extractWeights(p) }); setDropdownOpen(false); }}
              />
            ))}

            {/* AI-generated profiles */}
            {generated.length > 0 && (
              <>
                <div style={{ borderTop: "1px solid var(--border-subtle)", margin: "4px 0" }} />
                <div style={{ padding: "6px 10px 2px", fontSize: "10px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.08em", display: "flex", alignItems: "center", gap: "6px" }}>
                  AI-Generated
                  <span style={{ fontSize: "9px", padding: "1px 4px", borderRadius: "3px", background: "rgba(167,139,250,0.15)", color: "var(--accent-purple)", border: "1px solid rgba(167,139,250,0.3)" }}>
                    AI
                  </span>
                </div>
                {generated.map(p => (
                  <ProfileOption
                    key={p.id}
                    name={p.name}
                    active={activeProfileName === p.name}
                    badge={`${Math.round(p.confidence * 100)}%`}
                    onClick={() => { onProfileSelect({ type: "generated", key: `custom_${p.id}`, name: p.name, weights: extractCustomWeights(p) }); setDropdownOpen(false); }}
                  />
                ))}
              </>
            )}

            {/* User custom profiles */}
            {custom.length > 0 && (
              <>
                <div style={{ borderTop: "1px solid var(--border-subtle)", margin: "4px 0" }} />
                <div style={{ padding: "6px 10px 2px", fontSize: "10px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                  My Profiles
                </div>
                {custom.map(p => (
                  <ProfileOption
                    key={p.id}
                    name={p.name}
                    active={activeProfileName === p.name}
                    onClick={() => { onProfileSelect({ type: "custom", key: `custom_${p.id}`, name: p.name, weights: extractCustomWeights(p) }); setDropdownOpen(false); }}
                  />
                ))}
              </>
            )}

            {/* Create new */}
            <div style={{ borderTop: "1px solid var(--border-subtle)", margin: "4px 0" }} />
            <button
              onClick={() => { setDropdownOpen(false); onCreateProfile && onCreateProfile(); }}
              style={{
                width: "100%", textAlign: "left", background: "none",
                border: "none", color: "var(--accent-blue)", fontSize: "12px",
                padding: "8px 14px", cursor: "pointer", fontFamily: "inherit",
              }}
            >
              + Create New Profile
            </button>
          </div>
        )}
      </div>

      {/* Status */}
      <div style={{ flex: 1, display: "flex", alignItems: "center", gap: "12px" }}>
        {loading && (
          <span style={{ color: "var(--text-muted)", fontSize: "12px" }}>Analyzing...</span>
        )}
        {resolvedIndustry && !loading && (
          <span style={{ color: "var(--text-secondary)", fontSize: "12px" }}>
            Showing: <span style={{ color: "var(--accent-blue)", fontWeight: 500 }}>{resolvedIndustry.display_name}</span>
            {hasModifiers && (
              <span style={{ color: "var(--accent-amber)", marginLeft: "8px" }}>· Weights adjusted ↑</span>
            )}
          </span>
        )}
        {!resolvedIndustry && !loading && (
          <span style={{ color: "var(--text-muted)", fontSize: "12px" }}>
            Select a profile above or describe an industry in the chat →
          </span>
        )}
      </div>

      {/* Data completeness + sources */}
      <div style={{ display: "flex", alignItems: "center", gap: "10px", flexShrink: 0 }}>
        <span style={{ color: "var(--text-muted)", fontSize: "11px" }}>Data</span>
        <div style={{ width: "100px", height: "4px", background: "var(--border-subtle)", borderRadius: "2px", overflow: "hidden" }}>
          <div style={{ width: `${avgCompleteness}%`, height: "100%", background: "var(--accent-teal)", borderRadius: "2px" }} />
        </div>
        <span style={{ color: "var(--text-secondary)", fontSize: "11px", fontFamily: "JetBrains Mono, monospace" }}>{avgCompleteness}%</span>
        <button
          onClick={onSourcesClick}
          style={{
            background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)",
            color: "var(--text-accent)", fontSize: "11px", padding: "3px 8px",
            borderRadius: "4px", cursor: "pointer", fontFamily: "inherit",
          }}
        >
          ⊕ Sources
        </button>
      </div>
    </header>
  );
}

function ProfileOption({ name, active, badge, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        width: "100%", textAlign: "left", background: active ? "rgba(56,189,248,0.08)" : "none",
        border: "none", color: active ? "var(--accent-blue)" : "var(--text-secondary)",
        fontSize: "12px", padding: "7px 14px", cursor: "pointer", fontFamily: "inherit",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        transition: "background 0.1s",
      }}
      onMouseEnter={e => { if (!active) e.currentTarget.style.background = "rgba(255,255,255,0.04)"; }}
      onMouseLeave={e => { if (!active) e.currentTarget.style.background = "none"; }}
    >
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</span>
      {badge && <span style={{ fontSize: "10px", color: "var(--text-muted)", marginLeft: "6px", flexShrink: 0 }}>{badge}</span>}
    </button>
  );
}

function extractWeights(profile) {
  return Object.fromEntries(Object.entries(profile).filter(([k]) => k.startsWith("weight_")));
}

function extractCustomWeights(profile) {
  const keys = [
    "weight_literacy", "weight_road_density", "weight_highway_access",
    "weight_rail_connectivity", "weight_port_proximity", "weight_power",
    "weight_water", "weight_crop_output", "weight_crop_diversity",
    "weight_irrigation", "weight_industrial_estates", "weight_msme",
    "weight_land_affordability",
  ];
  return Object.fromEntries(keys.map(k => [k, profile[k] || 0]));
}
