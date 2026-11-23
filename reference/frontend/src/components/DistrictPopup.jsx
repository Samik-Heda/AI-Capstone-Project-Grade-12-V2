import { useState, useEffect, useRef } from "react";

const API = "/api";

const FIELD_LABELS = {
  literacy: "Literacy Rate",
  road_density: "Road Density",
  highway_access: "Highway Access",
  rail_connectivity: "Rail Connectivity",
  port_proximity: "Port Proximity",
  power_availability: "Power Supply",
  water_availability: "Water Access",
  crop_output: "Crop Output",
  crop_diversity: "Crop Diversity",
  irrigation_coverage: "Irrigation",
  industrial_estate: "Industrial Estates",
  msme: "MSME Count",
};

export default function DistrictPopup({
  districtId, rank, score, industry, weightVector, offset, ollamaOk, onClose, onAskInChat,
}) {
  const [district, setDistrict] = useState(null);
  const [pos, setPos] = useState({ x: 260 + offset, y: 70 + offset });
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef(null);
  const [aiText, setAiText] = useState("");
  const [aiStreaming, setAiStreaming] = useState(false);
  const [aiError, setAiError] = useState(false);

  useEffect(() => {
    fetch(`${API}/district/${districtId}`)
      .then((r) => r.json())
      .then(setDistrict);
  }, [districtId]);

  useEffect(() => {
    if (!district || !industry || !weightVector || !ollamaOk) return;
    streamExplanation();
  }, [district?.id, industry?.key]);

  const streamExplanation = async () => {
    setAiStreaming(true);
    setAiText("");
    setAiError(false);
    try {
      const res = await fetch(`${API}/explain`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          district_id: districtId,
          industry_key: industry?.key,
          rank: rank || 0,
          score: score || 0,
          weight_vector: weightVector || {},
          chat_history: [],
        }),
      });
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let text = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const line of decoder.decode(value).split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6);
          if (payload === "[DONE]") break;
          try { text += JSON.parse(payload).token; setAiText(text); } catch {}
        }
      }
    } catch {
      setAiError(true);
    } finally {
      setAiStreaming(false);
    }
  };

  // Drag handling
  const onMouseDown = (e) => {
    setDragging(true);
    dragStart.current = { mx: e.clientX, my: e.clientY, px: pos.x, py: pos.y };
  };
  useEffect(() => {
    const move = (e) => {
      if (!dragging || !dragStart.current) return;
      setPos({
        x: Math.max(0, dragStart.current.px + e.clientX - dragStart.current.mx),
        y: Math.max(0, dragStart.current.py + e.clientY - dragStart.current.my),
      });
    };
    const up = () => setDragging(false);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
  }, [dragging]);

  if (!district) return (
    <PopupShell pos={pos} onMouseDown={onMouseDown} onClose={onClose}>
      <div style={{ padding: "32px", textAlign: "center", color: "var(--text-muted)", fontSize: "12px" }}>
        Loading district data...
      </div>
    </PopupShell>
  );

  const breakdown = buildBreakdown(district, weightVector);
  const strengths = [...breakdown].sort((a, b) => b.contribution - a.contribution).slice(0, 3);
  const weaknesses = [...breakdown].sort((a, b) => a.contribution - b.contribution).slice(0, 2);

  return (
    <PopupShell pos={pos} onMouseDown={onMouseDown} onClose={onClose} dragging={dragging}>
      <div style={{ overflowY: "auto", flex: 1, padding: "14px 16px 16px" }}>

        {/* District header */}
        <div style={{ marginBottom: "10px" }}>
          <div style={{ fontSize: "19px", fontWeight: 700, color: "var(--text-primary)", lineHeight: 1.2 }}>
            {district.name}
          </div>
          <div style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "3px" }}>
            {district.region}
            {industry && <span style={{ color: "var(--text-muted)" }}> · {industry.display_name}</span>}
          </div>
        </div>

        {/* Rank + Score + Completeness row */}
        <div style={{ display: "flex", gap: "10px", marginBottom: "12px" }}>
          {rank && (
            <div style={{
              textAlign: "center", background: "var(--bg-elevated)",
              borderRadius: "8px", padding: "8px 12px", minWidth: "58px",
            }}>
              <div style={{ fontSize: "26px", fontWeight: 700, fontFamily: "JetBrains Mono,monospace", color: "var(--accent-blue)", lineHeight: 1 }}>
                #{rank}
              </div>
              <div style={{ fontSize: "9px", color: "var(--text-muted)", marginTop: "2px" }}>of 36</div>
            </div>
          )}
          <div style={{ flex: 1 }}>
            {score != null && (
              <MetricBar label="Score" value={score} max={100} displayValue={`${score.toFixed(1)} / 100`} color="linear-gradient(90deg,var(--accent-blue),var(--accent-teal))" />
            )}
            <MetricBar label="Data" value={district.data_completeness_pct} max={100} displayValue={`${district.data_completeness_pct}%`} color="var(--accent-teal)" />
          </div>
        </div>

        <Divider />

        {/* Strengths & Weaknesses — compact two-column */}
        <div style={{ display: "flex", gap: "10px", marginBottom: "10px" }}>
          <div style={{ flex: 1 }}>
            <SectionLabel color="var(--accent-green)">+ STRENGTHS</SectionLabel>
            {strengths.map((s) => (
              <BulletItem key={s.field} color="var(--accent-green)">
                <strong>{s.label}:</strong> {formatRaw(s.field, s.raw)}
                <span style={{ color: "var(--text-muted)", fontSize: "10px" }}> ({s.contribution.toFixed(1)}pts)</span>
              </BulletItem>
            ))}
          </div>
          <div style={{ flex: 1 }}>
            <SectionLabel color="var(--accent-rose)">− WEAKNESSES</SectionLabel>
            {weaknesses.map((w) => (
              <BulletItem key={w.field} color="var(--accent-rose)">
                <strong>{w.label}:</strong> {formatRaw(w.field, w.raw)}
                <span style={{ color: "var(--text-muted)", fontSize: "10px" }}> ({w.contribution.toFixed(1)}pts)</span>
              </BulletItem>
            ))}
          </div>
        </div>

        <Divider />

        {/* Score breakdown — compact bars */}
        <SectionLabel>Score Breakdown</SectionLabel>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 16px", marginBottom: "10px" }}>
          {[...breakdown].sort((a, b) => b.contribution - a.contribution).map((b) => (
            <div key={b.field}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "1px" }}>
                <span style={{ fontSize: "10px", color: "var(--text-secondary)" }}>{b.label}</span>
                <span style={{ fontSize: "10px", fontFamily: "JetBrains Mono,monospace", color: "var(--text-muted)" }}>
                  {b.contribution.toFixed(1)}
                </span>
              </div>
              <div style={{ height: "3px", background: "var(--border-subtle)", borderRadius: "2px", overflow: "hidden" }}>
                <div style={{ width: `${Math.min(100, b.norm * 100)}%`, height: "100%", background: "var(--accent-blue)", borderRadius: "2px" }} />
              </div>
            </div>
          ))}
        </div>

        <Divider />

        {/* AI Explanation */}
        <SectionLabel>AI Explanation</SectionLabel>
        <div style={{
          fontSize: "12px", lineHeight: 1.65, color: "var(--text-primary)",
          maxHeight: "140px", overflowY: "auto",
          padding: "8px 10px", background: "var(--bg-elevated)",
          borderRadius: "6px", marginBottom: "10px",
        }}>
          {!ollamaOk ? (
            <span style={{ color: "var(--text-muted)", fontStyle: "italic" }}>
              AI explanations unavailable — Ollama is offline.
            </span>
          ) : !industry ? (
            <span style={{ color: "var(--text-muted)", fontStyle: "italic" }}>
              Run a query to generate an explanation.
            </span>
          ) : aiError ? (
            <span style={{ color: "var(--text-muted)" }}>
              Could not generate explanation.{" "}
              <button onClick={streamExplanation} style={{ background: "none", border: "none", color: "var(--accent-blue)", cursor: "pointer", fontSize: "12px", padding: 0 }}>
                Retry
              </button>
            </span>
          ) : aiText ? (
            <>
              {aiText}
              {aiStreaming && <span className="cursor-blink" style={{ color: "var(--accent-teal)" }}>▊</span>}
            </>
          ) : (
            <span className="shimmer" style={{ color: "var(--text-muted)" }}>
              {aiStreaming ? "Generating explanation..." : "..."}
            </span>
          )}
        </div>

        {/* Ask in chat */}
      </div>
    </PopupShell>
  );
}

function PopupShell({ pos, onMouseDown, onClose, children, dragging }) {
  return (
    <div style={{
      position: "fixed", left: pos.x, top: pos.y, width: "360px", zIndex: 100,
      background: "var(--bg-overlay)", border: "1px solid var(--border-active)",
      borderRadius: "12px", boxShadow: "0 8px 40px rgba(0,0,0,0.7)",
      maxHeight: "88vh", display: "flex", flexDirection: "column",
      userSelect: dragging ? "none" : "auto",
    }}>
      <div
        onMouseDown={onMouseDown}
        style={{
          background: "var(--bg-surface)", borderRadius: "12px 12px 0 0",
          padding: "7px 12px", display: "flex", justifyContent: "space-between",
          alignItems: "center", cursor: dragging ? "grabbing" : "grab",
          borderBottom: "1px solid var(--border-subtle)", flexShrink: 0,
        }}
      >
        <span style={{ color: "var(--text-muted)", fontSize: "13px", letterSpacing: "3px" }}>⠿</span>
        <button onClick={onClose} style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: "16px", lineHeight: 1, padding: "0 2px" }}>×</button>
      </div>
      {children}
    </div>
  );
}

function MetricBar({ label, value, max, displayValue, color }) {
  return (
    <div style={{ marginBottom: "5px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "2px" }}>
        <span style={{ fontSize: "10px", color: "var(--text-muted)" }}>{label}</span>
        <span style={{ fontSize: "10px", fontFamily: "JetBrains Mono,monospace", color: "var(--text-secondary)" }}>{displayValue}</span>
      </div>
      <div style={{ height: "4px", background: "var(--border-subtle)", borderRadius: "2px", overflow: "hidden" }}>
        <div style={{ width: `${(value / max) * 100}%`, height: "100%", background: color, borderRadius: "2px" }} />
      </div>
    </div>
  );
}

function Divider() {
  return <div style={{ height: "1px", background: "var(--border-subtle)", margin: "8px 0" }} />;
}

function SectionLabel({ children, color }) {
  return (
    <div style={{
      fontSize: "9px", fontWeight: 600, textTransform: "uppercase",
      letterSpacing: "0.08em", color: color || "var(--text-muted)", marginBottom: "5px",
    }}>
      {children}
    </div>
  );
}

function BulletItem({ children, color }) {
  return (
    <div style={{ display: "flex", gap: "5px", marginBottom: "3px", fontSize: "11px", color: "var(--text-primary)", lineHeight: 1.4 }}>
      <span style={{ color, flexShrink: 0, marginTop: "1px" }}>·</span>
      <span>{children}</span>
    </div>
  );
}

function buildBreakdown(district, weightVector) {
  if (!weightVector) return [];
  const pairs = [
    { field: "literacy",          norm: district.literacy_norm,           raw: district.literacy_rate,          wk: "weight_literacy" },
    { field: "road_density",      norm: district.road_density_norm,       raw: district.road_density,           wk: "weight_road_density" },
    { field: "highway_access",    norm: district.highway_access_norm,     raw: district.highway_access,         wk: "weight_highway_access" },
    { field: "rail_connectivity", norm: district.rail_connectivity_norm,  raw: district.rail_connectivity,      wk: "weight_rail_connectivity" },
    { field: "port_proximity",    norm: district.port_proximity_norm,     raw: district.port_proximity_km,      wk: "weight_port_proximity" },
    { field: "power_availability",norm: district.power_availability_norm, raw: district.power_availability,    wk: "weight_power" },
    { field: "water_availability",norm: district.water_availability_norm, raw: district.water_availability,    wk: "weight_water" },
    { field: "crop_output",       norm: district.crop_output_norm,        raw: district.crop_output_mt,         wk: "weight_crop_output" },
    { field: "crop_diversity",    norm: district.crop_diversity_norm,     raw: district.crop_diversity_index,  wk: "weight_crop_diversity" },
    { field: "irrigation_coverage",norm: district.irrigation_coverage_norm,raw: district.irrigation_coverage,  wk: "weight_irrigation" },
    { field: "industrial_estate", norm: district.industrial_estate_norm,  raw: district.industrial_estate_count,wk: "weight_industrial_estates" },
    { field: "msme",              norm: district.msme_norm,               raw: district.msme_count,             wk: "weight_msme" },
  ];
  return pairs
    .map((p) => ({
      ...p,
      label: FIELD_LABELS[p.field] || p.field,
      weight: weightVector[p.wk] || 0,
      contribution: (p.norm || 0) * (weightVector[p.wk] || 0) * 100,
    }))
    .filter((p) => p.weight > 0);
}

function formatRaw(field, raw) {
  if (raw == null) return "N/A";
  if (field === "crop_output") return `${(raw / 1_000_000).toFixed(1)}M MT`;
  if (field === "msme") return Number(raw).toLocaleString();
  if (field === "port_proximity") return `${Math.round(raw)} km`;
  if (field === "literacy") return `${raw}%`;
  if (field === "irrigation_coverage") return `${raw}%`;
  if (field === "highway_access") return raw >= 2 ? "National HW" : raw >= 1 ? "State HW" : "None";
  if (field === "rail_connectivity") return raw >= 2 ? "Major jxn" : raw >= 1 ? "Minor line" : "None";
  return typeof raw === "number" ? raw.toFixed(1) : String(raw);
}
