const SOURCES = [
  { field: "Population", source: "Census of India, District-wise", year: 2011, status: "Sourced" },
  { field: "Literacy Rate", source: "Census of India, District-wise", year: 2011, status: "Sourced" },
  { field: "Road Density", source: "MoRTH District Road Statistics", year: 2022, status: "Sourced" },
  { field: "Highway Access", source: "NITI Aayog Connectivity Index", year: 2023, status: "Sourced" },
  { field: "Rail Connectivity", source: "Indian Railways Zone Map", year: 2023, status: "Derived" },
  { field: "Port Proximity", source: "Computed from district centroid", year: "—", status: "Derived" },
  { field: "Power Availability", source: "MSEDCL Annual District Report", year: 2023, status: "Sourced" },
  { field: "Water Availability", source: "MWRRA / CWC Basin Data", year: 2021, status: "Sourced" },
  { field: "Crop Output", source: "MAHA-AGRI (3-yr avg 2019–22)", year: 2022, status: "Sourced" },
  { field: "Crop Diversity", source: "Derived from MAHA-AGRI crop register", year: 2022, status: "Derived" },
  { field: "Irrigation Coverage", source: "Minor Irrigation Census", year: 2017, status: "Sourced" },
  { field: "Industrial Estates", source: "MIDC District Directory", year: 2023, status: "Sourced" },
  { field: "MSME Count", source: "MSME Ministry District Register", year: 2021, status: "Sourced" },
];

const STATUS_COLOR = {
  Sourced: "var(--accent-green)",
  Derived: "var(--accent-blue)",
  Estimated: "var(--accent-amber)",
};

export default function DataSourcesModal({ onClose }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 200,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "580px", maxHeight: "75vh", background: "var(--bg-overlay)",
          border: "1px solid var(--border-active)", borderRadius: "12px",
          overflow: "hidden", display: "flex", flexDirection: "column",
          boxShadow: "0 8px 40px rgba(0,0,0,0.7)",
        }}
      >
        <div style={{
          padding: "14px 20px", borderBottom: "1px solid var(--border-subtle)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <span style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-primary)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Data Sources
          </span>
          <button onClick={onClose} style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: "18px" }}>×</button>
        </div>

        <div style={{ overflowY: "auto", padding: "14px 20px" }}>
          <p style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "14px", lineHeight: 1.6 }}>
            All district data is sourced from Indian government publications. Estimated fields are marked with ◌.
            Missing values are imputed using the regional average of districts in the same geographical region.
          </p>

          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                {["Field", "Source", "Year", "Status"].map((h) => (
                  <th key={h} style={{ textAlign: "left", padding: "4px 8px", color: "var(--text-muted)", fontSize: "10px", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {SOURCES.map((s, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                  <td style={{ padding: "6px 8px", color: "var(--text-primary)" }}>{s.field}</td>
                  <td style={{ padding: "6px 8px", color: "var(--text-secondary)" }}>{s.source}</td>
                  <td style={{ padding: "6px 8px", color: "var(--text-muted)", fontFamily: "JetBrains Mono, monospace" }}>{s.year}</td>
                  <td style={{ padding: "6px 8px" }}>
                    <span style={{ color: STATUS_COLOR[s.status], fontWeight: 500 }}>{s.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div style={{ marginTop: "14px", padding: "10px 12px", background: "var(--bg-elevated)", borderRadius: "6px", fontSize: "12px", color: "var(--text-secondary)", lineHeight: 1.6 }}>
            <strong style={{ color: "var(--text-primary)" }}>Ports used for proximity calculation:</strong><br />
            JNPT (Raigad) · Mumbai Port Trust · Dighi Port (Raigad) · Jaigad Port (Ratnagiri) · Ratnagiri Port
          </div>
        </div>
      </div>
    </div>
  );
}
