import { useState, useEffect } from "react";

const API = "/api";

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

function ConfidenceBar({ confidence, label }) {
  const color = label === "High" ? "var(--accent-green)" : label === "Medium" ? "var(--accent-amber)" : "var(--accent-rose)";
  return (
    <div style={{ marginBottom: "14px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
        <span style={{ fontSize: "11px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
          Profile Confidence
        </span>
        <span style={{ fontSize: "11px", fontWeight: 600, color, fontFamily: "JetBrains Mono, monospace" }}>
          {label} — {Math.round(confidence * 100)}%
        </span>
      </div>
      <div style={{ height: "5px", background: "var(--border-subtle)", borderRadius: "3px", overflow: "hidden" }}>
        <div style={{ width: `${confidence * 100}%`, height: "100%", background: color, borderRadius: "3px", transition: "width 0.5s ease" }} />
      </div>
    </div>
  );
}

export default function ProfileBuilderModal({ allProfiles, onClose, onRankWithProfile, onProfileSaved }) {
  const [baseKey, setBaseKey] = useState("");
  const [description, setDescription] = useState("");
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState(null);
  const [adjustText, setAdjustText] = useState("");
  const [profileName, setProfileName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const standardProfiles = allProfiles?.standard || [];
  const generatedProfiles = allProfiles?.generated || [];

  const selectedBase = [...standardProfiles, ...generatedProfiles].find(
    p => (p.industry_key || `custom_${p.id}`) === baseKey
  );

  const handleGenerate = async () => {
    if (!description.trim()) return;
    setGenerating(true);
    setError("");
    try {
      const res = await fetch(`${API}/profile/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description: description.trim(),
          base_industry_key: baseKey || null,
        }),
      });
      const data = await res.json();
      if (data.weights) {
        setResult(data);
        if (!profileName) {
          const words = description.trim().split(/\s+/).slice(0, 3).join(" ");
          setProfileName(words.charAt(0).toUpperCase() + words.slice(1));
        }
      }
    } catch (e) {
      setError("Failed to generate profile. Is the backend running?");
    } finally {
      setGenerating(false);
    }
  };

  const handleAdjust = async () => {
    if (!adjustText.trim() || !result) return;
    const combined = `${description} ${adjustText}`;
    setGenerating(true);
    try {
      const res = await fetch(`${API}/profile/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description: combined.trim(),
          base_industry_key: baseKey || null,
        }),
      });
      const data = await res.json();
      if (data.weights) setResult(data);
      setAdjustText("");
    } catch {}
    setGenerating(false);
  };

  const handleSave = async () => {
    if (!result || !profileName.trim()) return;
    setSaving(true);
    try {
      const res = await fetch(`${API}/profile/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: profileName.trim(),
          weights: result.weights,
          base_industry_key: baseKey || null,
          description: description.trim(),
          confidence: result.confidence,
        }),
      });
      const data = await res.json();
      onProfileSaved && onProfileSaved({ id: data.id, name: data.name, weights: result.weights });
    } catch {}
    setSaving(false);
  };

  const handleRankNow = () => {
    if (!result) return;
    onRankWithProfile({
      display_name: profileName.trim() || "Custom Profile",
      weights: result.weights,
    });
    onClose();
  };

  const baseWeights = selectedBase
    ? Object.fromEntries(Object.entries(selectedBase).filter(([k]) => k.startsWith("weight_")))
    : null;

  const sortedWeights = result
    ? Object.entries(result.weights).sort(([, a], [, b]) => b - a)
    : [];

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 100,
      display: "flex", justifyContent: "flex-end",
    }} onClick={(e) => e.target === e.currentTarget && onClose()}>
      {/* Backdrop */}
      <div style={{ position: "absolute", inset: 0, background: "rgba(11,17,32,0.6)" }} onClick={onClose} />

      {/* Drawer */}
      <div style={{
        position: "relative", width: "400px", height: "100%",
        background: "var(--bg-surface)", borderLeft: "1px solid var(--border-subtle)",
        display: "flex", flexDirection: "column", overflow: "hidden",
        boxShadow: "-8px 0 32px rgba(0,0,0,0.5)",
      }}>
        {/* Header */}
        <div style={{
          padding: "16px", borderBottom: "1px solid var(--border-subtle)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          flexShrink: 0,
        }}>
          <div>
            <div style={{ fontSize: "14px", fontWeight: 600, color: "var(--text-primary)" }}>
              Build Industry Profile
            </div>
            <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "2px" }}>
              AI generates weights from your description
            </div>
          </div>
          <button onClick={onClose} style={{
            background: "none", border: "none", color: "var(--text-muted)",
            fontSize: "18px", cursor: "pointer", padding: "4px 8px",
          }}>×</button>
        </div>

        {/* Scrollable body */}
        <div style={{ flex: 1, overflowY: "auto", padding: "16px" }}>

          {/* Section 1 — Base */}
          <div style={{ marginBottom: "16px" }}>
            <label style={{ fontSize: "11px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", display: "block", marginBottom: "6px" }}>
              Start from
            </label>
            <select
              value={baseKey}
              onChange={e => setBaseKey(e.target.value)}
              style={{
                width: "100%", background: "var(--bg-elevated)",
                border: "1px solid var(--border-subtle)", color: "var(--text-primary)",
                borderRadius: "6px", padding: "7px 10px", fontSize: "12px",
                fontFamily: "inherit", cursor: "pointer",
              }}
            >
              <option value="">Scratch — no base profile</option>
              <optgroup label="Standard Profiles">
                {standardProfiles.map(p => (
                  <option key={p.industry_key} value={p.industry_key}>{p.display_name}</option>
                ))}
              </optgroup>
              {generatedProfiles.length > 0 && (
                <optgroup label="AI-Generated Profiles">
                  {generatedProfiles.map(p => (
                    <option key={`custom_${p.id}`} value={`custom_${p.id}`}>{p.name}</option>
                  ))}
                </optgroup>
              )}
            </select>
            {selectedBase && baseWeights && (
              <div style={{ marginTop: "8px", fontSize: "10px", color: "var(--text-muted)" }}>
                Base: {Object.entries(baseWeights).filter(([,v]) => v > 0.02).sort(([,a],[,b]) => b-a).slice(0, 4).map(([k, v]) => `${WEIGHT_LABELS[k]} ${Math.round(v*100)}%`).join(", ")}
              </div>
            )}
          </div>

          {/* Section 2 — Describe */}
          <div style={{ marginBottom: "16px" }}>
            <label style={{ fontSize: "11px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", display: "block", marginBottom: "6px" }}>
              Describe your industry
            </label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="e.g. We manufacture medical devices requiring clean water, stable power, and a skilled engineering workforce near major highways."
              rows={4}
              style={{
                width: "100%", background: "var(--bg-elevated)",
                border: "1px solid var(--border-subtle)", color: "var(--text-primary)",
                borderRadius: "6px", padding: "8px 10px", fontSize: "12px",
                fontFamily: "inherit", resize: "vertical", boxSizing: "border-box",
                lineHeight: "1.5",
              }}
              onKeyDown={e => { if (e.key === "Enter" && e.ctrlKey) handleGenerate(); }}
            />
            <button
              onClick={handleGenerate}
              disabled={!description.trim() || generating}
              style={{
                marginTop: "8px", width: "100%",
                background: description.trim() && !generating ? "var(--accent-blue)" : "var(--bg-overlay)",
                border: "none", color: description.trim() && !generating ? "#0B1120" : "var(--text-muted)",
                borderRadius: "6px", padding: "8px", fontSize: "12px", fontWeight: 600,
                cursor: description.trim() && !generating ? "pointer" : "not-allowed",
                fontFamily: "inherit", transition: "background 0.2s",
              }}
            >
              {generating ? "Generating..." : "Generate Weights ▸"}
            </button>
            {error && <div style={{ marginTop: "6px", fontSize: "11px", color: "var(--accent-rose)" }}>{error}</div>}
          </div>

          {/* Section 3 — Generated Weights */}
          {result && (
            <div style={{ marginBottom: "16px" }}>
              <div style={{ borderTop: "1px solid var(--border-subtle)", paddingTop: "16px" }}>
                <ConfidenceBar confidence={result.confidence} label={result.confidence_label} />

                {result.base_display_name && (
                  <div style={{ fontSize: "10px", color: "var(--text-muted)", fontStyle: "italic", marginBottom: "10px" }}>
                    Derived from {result.base_display_name} — 60% description + 40% base
                  </div>
                )}

                {/* Weight bars */}
                <div style={{ marginBottom: "12px" }}>
                  {sortedWeights.filter(([, v]) => v > 0.005).map(([key, value]) => {
                    const pct = Math.round(value * 100);
                    const basePct = baseWeights ? Math.round((baseWeights[key] || 0) * 100) : null;
                    return (
                      <div key={key} style={{ marginBottom: "7px" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "2px" }}>
                          <span style={{ fontSize: "11px", color: "var(--text-secondary)" }}>{WEIGHT_LABELS[key]}</span>
                          <span style={{ fontSize: "11px", fontFamily: "JetBrains Mono, monospace", color: "var(--accent-blue)" }}>
                            {pct}%
                            {basePct !== null && Math.abs(pct - basePct) > 1 && (
                              <span style={{ color: pct > basePct ? "var(--accent-green)" : "var(--accent-rose)", marginLeft: "4px", fontSize: "10px" }}>
                                {pct > basePct ? `+${pct - basePct}` : `${pct - basePct}`}
                              </span>
                            )}
                          </span>
                        </div>
                        <div style={{ position: "relative", height: "4px", background: "var(--border-subtle)", borderRadius: "2px", overflow: "hidden" }}>
                          <div style={{ width: `${pct}%`, height: "100%", background: "var(--accent-blue)", borderRadius: "2px", transition: "width 0.4s ease" }} />
                          {basePct !== null && (
                            <div style={{
                              position: "absolute", top: 0, left: `${basePct}%`,
                              width: "2px", height: "100%", background: "rgba(148,163,184,0.5)",
                            }} />
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {/* Natural language adjustment */}
                <div style={{ display: "flex", gap: "6px" }}>
                  <input
                    value={adjustText}
                    onChange={e => setAdjustText(e.target.value)}
                    placeholder="Adjust: e.g. make water more important"
                    style={{
                      flex: 1, background: "var(--bg-elevated)",
                      border: "1px solid var(--border-subtle)", color: "var(--text-primary)",
                      borderRadius: "6px", padding: "6px 8px", fontSize: "11px",
                      fontFamily: "inherit",
                    }}
                    onKeyDown={e => { if (e.key === "Enter") handleAdjust(); }}
                  />
                  <button onClick={handleAdjust} disabled={!adjustText.trim() || generating} style={{
                    background: "var(--bg-overlay)", border: "1px solid var(--border-subtle)",
                    color: "var(--text-secondary)", borderRadius: "6px", padding: "6px 10px",
                    fontSize: "11px", cursor: "pointer", fontFamily: "inherit",
                  }}>
                    Apply
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Section 4 — Save */}
          {result && (
            <div style={{ borderTop: "1px solid var(--border-subtle)", paddingTop: "16px" }}>
              <label style={{ fontSize: "11px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em", display: "block", marginBottom: "6px" }}>
                Profile name
              </label>
              <input
                value={profileName}
                onChange={e => setProfileName(e.target.value)}
                placeholder="e.g. Pharmaceutical Plant"
                style={{
                  width: "100%", background: "var(--bg-elevated)",
                  border: "1px solid var(--border-subtle)", color: "var(--text-primary)",
                  borderRadius: "6px", padding: "7px 10px", fontSize: "12px",
                  fontFamily: "inherit", boxSizing: "border-box", marginBottom: "8px",
                }}
              />
              <div style={{ display: "flex", gap: "8px" }}>
                <button onClick={handleSave} disabled={!profileName.trim() || saving} style={{
                  flex: 1, background: "var(--bg-overlay)", border: "1px solid var(--border-subtle)",
                  color: "var(--text-secondary)", borderRadius: "6px", padding: "8px",
                  fontSize: "12px", cursor: "pointer", fontFamily: "inherit",
                }}>
                  {saving ? "Saving..." : "Save Profile"}
                </button>
                <button onClick={handleRankNow} style={{
                  flex: 1, background: "var(--accent-blue)", border: "none",
                  color: "#0B1120", borderRadius: "6px", padding: "8px",
                  fontSize: "12px", fontWeight: 600, cursor: "pointer", fontFamily: "inherit",
                }}>
                  Rank Now →
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
