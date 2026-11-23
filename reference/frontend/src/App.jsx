import { useState, useEffect, useCallback, useRef } from "react";
import Header from "./components/Header.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import MapPanel from "./components/MapPanel.jsx";
import DistrictPopup from "./components/DistrictPopup.jsx";
import DataSourcesModal from "./components/DataSourcesModal.jsx";
import ProfileBuilderModal from "./components/ProfileBuilderModal.jsx";

const API = "/api";

export default function App() {
  const [ollamaOk, setOllamaOk] = useState(true);
  const [rankings, setRankings] = useState([]);
  const [resolvedIndustry, setResolvedIndustry] = useState(null);
  const [weightVector, setWeightVector] = useState(null);
  const [baseWeightVector, setBaseWeightVector] = useState(null);
  const baseWeightVectorRef = useRef(null);
  const [weightModifiers, setWeightModifiers] = useState({});
  const weightModifiersRef = useRef({});
  const [loading, setLoading] = useState(false);
  const [openPopups, setOpenPopups] = useState([]);
  const [showSources, setShowSources] = useState(false);
  const [pendingConfirm, setPendingConfirm] = useState(null);
  const [chatPrefill, setChatPrefill] = useState("");

  // Profile system
  const [allProfiles, setAllProfiles] = useState({ standard: [], generated: [], custom: [] });
  const [activeProfileName, setActiveProfileName] = useState(null);
  const [showProfileBuilder, setShowProfileBuilder] = useState(false);

  // Session weight learning: { "[industryKey]:[weightKey]": count }
  const [sessionSignals, setSessionSignals] = useState({});
  const sessionSignalsRef = useRef({});
  // Set of weight_keys that have been AI-adapted this session
  const [learnedFields, setLearnedFields] = useState(new Set());

  useEffect(() => {
    fetch(`${API}/health`)
      .then((r) => r.json())
      .then((d) => setOllamaOk(d.ollama === "ok"))
      .catch(() => setOllamaOk(false));

    fetch(`${API}/profile/list`)
      .then((r) => r.json())
      .then((d) => setAllProfiles(d))
      .catch(() => {});
  }, []);

  const refreshProfiles = useCallback(() => {
    fetch(`${API}/profile/list`)
      .then((r) => r.json())
      .then((d) => setAllProfiles(d))
      .catch(() => {});
  }, []);

  const runQuery = useCallback(async (query, opts = {}) => {
    const { silent = false, industryKeyOverride = null } = typeof opts === "string"
      ? { industryKeyOverride: opts }
      : opts;
    if (!silent) setLoading(true);
    setPendingConfirm(null);
    try {
      let body, url;
      if (industryKeyOverride) {
        url = `${API}/query/confirm`;
        const accMods = {};
        for (const [k, v] of Object.entries(weightModifiersRef.current)) {
          accMods[k] = typeof v === "object" ? v.modifier : v;
        }
        body = { 
          query, 
          industry_key: industryKeyOverride, 
          top_n: 10, 
          accumulated_modifiers: accMods,
          base_weight_vector: baseWeightVectorRef.current,
          industry_display_name: activeProfileName
        };
      } else {
        url = `${API}/query`;
        const accMods = {};
        for (const [k, v] of Object.entries(weightModifiersRef.current)) {
          accMods[k] = typeof v === "object" ? v.modifier : v;
        }
        body = { 
          query, 
          top_n: 10,
          current_industry_key: resolvedIndustry ? resolvedIndustry.key : null,
          accumulated_modifiers: accMods,
          base_weight_vector: baseWeightVectorRef.current,
          industry_display_name: activeProfileName
        };
      }
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();

      if (data.status === "confirmation_required") {
        setPendingConfirm({ query, resolved: data.resolved_industry, message: data.message });
        return { type: "confirmation_required", data };
      }
      if (data.status === "no_match") return { type: "no_match", data };

      setRankings(data.rankings || []);
      setResolvedIndustry(data.resolved_industry);
      setWeightVector(data.weight_vector);
      if (data.base_weight_vector) {
        setBaseWeightVector(data.base_weight_vector);
        baseWeightVectorRef.current = data.base_weight_vector;
      }
      setActiveProfileName(data.resolved_industry?.display_name || null);
      const newMods = data.weight_modifiers_applied || {};
      setWeightModifiers(newMods);
      weightModifiersRef.current = newMods;
      return { type: "ok", data };
    } catch (e) {
      return { type: "error", error: e.message };
    } finally {
      setLoading(false);
    }
  }, []);

  // Rank using a directly-supplied weight vector (custom profiles)
  const rankWithProfile = useCallback(async (profile) => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/rank/custom`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ weight_vector: profile.weights, display_name: profile.display_name, top_n: 10 }),
      });
      const data = await res.json();
      if (data.status === "ok") {
        setRankings(data.rankings || []);
        setResolvedIndustry(data.resolved_industry);
        setWeightVector(data.weight_vector);
        setBaseWeightVector(data.weight_vector); // Custom profile base is the starting weights
        baseWeightVectorRef.current = data.weight_vector;
        setWeightModifiers({});
        weightModifiersRef.current = {};
        setActiveProfileName(profile.display_name);
      }
    } catch {}
    setLoading(false);
  }, []);

  // Session weight learning: called by ChatPanel after each weight_adjust
  const handleWeightSignal = useCallback((modKeys, industryKey) => {
    if (!industryKey || !modKeys?.length) return 0;

    const updated = { ...sessionSignalsRef.current };
    let maxCount = 0;
    const newLearned = new Set(learnedFields);

    for (const field of modKeys) {
      // Map field name to weight key (simplified)
      const wKey = `weight_${field}`;
      const sigKey = `${industryKey}:${wKey}`;
      updated[sigKey] = (updated[sigKey] || 0) + 1;
      maxCount = Math.max(maxCount, updated[sigKey]);

      // After 3 signals on same field, apply EMA update and mark as learned
      if (updated[sigKey] >= 3) {
        newLearned.add(wKey);
        // EMA: current weight already reflects user preference via the modifier system
        // Mark as learned so UI shows the ▴ AI badge
      }
    }

    sessionSignalsRef.current = updated;
    setSessionSignals({ ...updated });
    setLearnedFields(newLearned);
    return maxCount;
  }, [learnedFields]);

  // Handle profile selection from header dropdown
  const handleProfileSelect = useCallback(async (profile) => {
    if (profile.type === "standard") {
      // Use normal query flow so chat stays in sync
      await runQuery(profile.name, { industryKeyOverride: profile.key });
    } else {
      // Custom or generated — use direct weight ranking
      await rankWithProfile({ display_name: profile.name, weights: profile.weights });
    }
  }, [runQuery, rankWithProfile]);

  const openDistrictPopup = useCallback((districtId, rank, score) => {
    if (openPopups.some((p) => p.districtId === districtId)) return;
    const entry = { districtId, rank, score, industry: resolvedIndustry, weightVector };
    if (openPopups.length >= 2) {
      setOpenPopups([openPopups[1], entry]);
    } else {
      setOpenPopups((prev) => [...prev, entry]);
    }
  }, [openPopups, resolvedIndustry, weightVector]);

  const closePopup = useCallback((districtId) => {
    setOpenPopups((prev) => prev.filter((p) => p.districtId !== districtId));
  }, []);

  const resetWeights = useCallback(async () => {
    if (!resolvedIndustry) return;
    const res = await fetch(`${API}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: resolvedIndustry.display_name, top_n: 10 }),
    });
    const data = await res.json();
    if (data.rankings) {
      setRankings(data.rankings);
      setWeightVector(data.weight_vector);
      setWeightModifiers({});
      weightModifiersRef.current = {};
      setLearnedFields(new Set());
      sessionSignalsRef.current = {};
      setSessionSignals({});
    }
  }, [resolvedIndustry]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", background: "var(--bg-base)" }}>
      <NarrowBanner />

      <Header
        resolvedIndustry={resolvedIndustry}
        weightModifiers={weightModifiers}
        loading={loading}
        onSourcesClick={() => setShowSources(true)}
        rankings={rankings}
        allProfiles={allProfiles}
        activeProfileName={activeProfileName}
        onProfileSelect={handleProfileSelect}
        onCreateProfile={() => setShowProfileBuilder(true)}
      />

      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <ChatPanel
          ollamaOk={ollamaOk}
          loading={loading}
          resolvedIndustry={resolvedIndustry}
          rankings={rankings}
          weightVector={weightVector}
          weightModifiers={weightModifiers}
          pendingConfirm={pendingConfirm}
          prefill={chatPrefill}
          onPrefillUsed={() => setChatPrefill("")}
          onQuery={runQuery}
          onConfirm={(query, key) => runQuery(query, key)}
          onDistrictClick={openDistrictPopup}
          onWeightSignal={handleWeightSignal}
        />
        <MapPanel
          rankings={rankings}
          loading={loading}
          resolvedIndustry={resolvedIndustry}
          weightVector={weightVector}
          weightModifiers={weightModifiers}
          onDistrictClick={openDistrictPopup}
          onResetWeights={resetWeights}
          learnedFields={learnedFields}
        />
      </div>

      {openPopups.map((popup, i) => (
        <DistrictPopup
          key={popup.districtId}
          districtId={popup.districtId}
          rank={popup.rank}
          score={popup.score}
          industry={popup.industry}
          weightVector={popup.weightVector}
          offset={i * 24}
          ollamaOk={ollamaOk}
          onClose={() => closePopup(popup.districtId)}
          onAskInChat={(msg) => setChatPrefill(msg)}
        />
      ))}

      {showSources && <DataSourcesModal onClose={() => setShowSources(false)} />}

      {showProfileBuilder && (
        <ProfileBuilderModal
          allProfiles={allProfiles}
          onClose={() => setShowProfileBuilder(false)}
          onRankWithProfile={rankWithProfile}
          onProfileSaved={() => { refreshProfiles(); setShowProfileBuilder(false); }}
        />
      )}
    </div>
  );
}

function NarrowBanner() {
  const [dismissed, setDismissed] = useState(false);
  const [narrow, setNarrow] = useState(window.innerWidth < 1440);

  useEffect(() => {
    const handler = () => setNarrow(window.innerWidth < 1440);
    window.addEventListener("resize", handler);
    return () => window.removeEventListener("resize", handler);
  }, []);

  if (!narrow || dismissed) return null;
  return (
    <div style={{
      background: "var(--bg-elevated)", borderLeft: "3px solid var(--accent-amber)",
      padding: "8px 16px", display: "flex", justifyContent: "space-between",
      alignItems: "center", fontSize: "12px", color: "var(--text-secondary)",
    }}>
      <span>This dashboard is designed for screens 1440px or wider. Some elements may appear clipped.</span>
      <button onClick={() => setDismissed(true)} style={{ background: "none", border: "none", color: "var(--text-muted)", cursor: "pointer", fontSize: "14px" }}>×</button>
    </div>
  );
}
