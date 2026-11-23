import { useState, useRef, useEffect, useCallback } from "react";

const EXAMPLE_CHIPS = [
  { label: "Food processing", query: "I want to set up a food processing plant" },
  { label: "Automobile", query: "I'm looking for an automobile manufacturing hub" },
  { label: "Dairy", query: "dairy processing unit" },
  { label: "Textile", query: "textile factory" },
  { label: "General mfg", query: "general manufacturing facility" },
  { label: "High literacy", query: "general manufacturing where literacy is critical" },
];

// All 36 district names for detection
const ALL_DISTRICTS = [
  "Mumbai City", "Mumbai Suburban", "Thane", "Raigad", "Ratnagiri", "Sindhudurg", "Palghar",
  "Nashik", "Ahmednagar", "Dhule", "Nandurbar", "Jalgaon",
  "Pune", "Satara", "Sangli", "Solapur", "Kolhapur",
  "Aurangabad", "Jalna", "Beed", "Latur", "Osmanabad", "Nanded", "Hingoli", "Parbhani",
  "Nagpur", "Wardha", "Yavatmal", "Amravati", "Akola", "Washim", "Buldhana",
  "Chandrapur", "Gadchiroli", "Gondia", "Bhandara",
];

const FIELD_WORDS = /\bliteracy\b|\broad( density)?\b|\bpower\b|\bwater\b|\bcrop\b|\bport\b|\brail(way)?\b|\bmsme\b|\birrigation\b|\bindustrial( estate)?\b|\bhighway\b|\binfrastructure\b|\bland\b|\bcost\b|\bafford(ability)?\b|\breal estate\b/i;
const MOD_WORDS = /\bimportant(er)?\b|\bmore\b|\bless\b|\bcritical\b|\bignore\b|\bpriority\b|\bweight\b|\bfocus\b|\bemphasiz\b|\bincrease\b|\bdecrease\b|\bboost\b|\bmatters?\b|\bhigh\b|\blow\b|\bgood\b|\bgreat\b|\bstrong\b|\bweak\b|\bneed\b|\brequire\b|\bwant\b|\bprioritize\b/i;
const COMPARE_WORDS = /\bcompare\b|\bvs\.?\b|\bversus\b|\bbetter\b|\bworse\b|\bdifference\b|\bsimilar\b|\bover\b/i;
const EXPLAIN_WORDS = /^(why|how|what|tell me|explain|describe|show me|give me|list)/i;
const RANK_WORDS = /\b(rank(ed|ing)?|score|strength|weakness|advantage|disadvantage|performance)\b/i;

function classifyMessage(input, resolvedIndustry, rankings) {
  const q = input.trim();

  // Explicit comparison → chat
  if (COMPARE_WORDS.test(q)) return "chat";

  // Explanation question → chat
  if (EXPLAIN_WORDS.test(q)) return "chat";

  // Rank/score questions → chat
  if (RANK_WORDS.test(q) && resolvedIndustry) return "chat";

  // Weight modification → weight_adjust (re-rank silently, no blank)
  if (FIELD_WORDS.test(q) && MOD_WORDS.test(q)) return "weight_adjust";

  // District name mentioned with no apparent new industry → chat
  if (resolvedIndustry) {
    const hasDistrict = ALL_DISTRICTS.some(name =>
      q.toLowerCase().includes(name.toLowerCase())
    );
    if (hasDistrict) return "chat";
  }

  // Default: treat as new industry ranking query
  return "industry_query";
}

function extractMentionedDistricts(text) {
  return ALL_DISTRICTS.filter(name =>
    text.toLowerCase().includes(name.toLowerCase())
  );
}

export default function ChatPanel({
  ollamaOk, loading, resolvedIndustry, rankings, weightVector,
  weightModifiers, pendingConfirm, prefill, onPrefillUsed,
  onQuery, onConfirm, onDistrictClick, onWeightSignal,
}) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const chatHistoryRef = useRef([]); // tracks {role, content} for LLM context
  const bottomRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (prefill) {
      setInput(prefill);
      onPrefillUsed();
      inputRef.current?.focus();
    }
  }, [prefill, onPrefillUsed]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const addMsg = (role, content, meta = {}) => {
    const msg = { role, content, meta, time: new Date() };
    setMessages(prev => [...prev, msg]);
    return msg;
  };

  const updateLastMsg = (id, updater) => {
    setMessages(prev => prev.map(m => m._id === id ? { ...m, ...updater(m) } : m));
  };

  const streamChat = useCallback(async (userText) => {
    if (!ollamaOk) {
      addMsg("assistant", "[!] AI explanations unavailable — Ollama is offline.");
      return;
    }
    setStreaming(true);
    const msgId = Date.now();
    setMessages(prev => [...prev, { _id: msgId, role: "assistant", content: "", streaming: true }]);

    const mentioned = extractMentionedDistricts(userText);
    const history = chatHistoryRef.current.slice(-6);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: userText,
          industry_key: resolvedIndustry?.key || null,
          industry_display: resolvedIndustry?.display_name || null,
          weight_vector: weightVector || null,
          rankings: rankings.slice(0, 10),
          mentioned_districts: mentioned,
          chat_history: history,
        }),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

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
          try { text += JSON.parse(payload).token; } catch {}
        }
        setMessages(prev => prev.map(m => m._id === msgId ? { ...m, content: text } : m));
      }
      setMessages(prev => prev.map(m => m._id === msgId ? { ...m, streaming: false } : m));
      chatHistoryRef.current.push({ role: "user", content: userText }, { role: "assistant", content: text });
    } catch (e) {
      setMessages(prev => prev.map(m =>
        m._id === msgId ? { ...m, content: "Could not get a response. Check that Ollama is running.", streaming: false } : m
      ));
    } finally {
      setStreaming(false);
    }
  }, [ollamaOk, resolvedIndustry, weightVector, rankings]);

  const streamExplainForTop = useCallback(async (topDistrict, industryKey, wv) => {
    if (!ollamaOk || !topDistrict) return;
    setStreaming(true);
    const msgId = Date.now();
    setMessages(prev => [...prev, { _id: msgId, role: "assistant", content: "", streaming: true }]);
    try {
      const res = await fetch("/api/explain", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          district_id: topDistrict.district_id,
          industry_key: industryKey,
          rank: topDistrict.rank,
          score: topDistrict.score,
          weight_vector: wv,
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
          try { text += JSON.parse(payload).token; } catch {}
        }
        setMessages(prev => prev.map(m => m._id === msgId ? { ...m, content: text } : m));
      }
      setMessages(prev => prev.map(m => m._id === msgId ? { ...m, streaming: false } : m));
      chatHistoryRef.current.push({ role: "assistant", content: text });
    } catch {
      setMessages(prev => prev.map(m =>
        m._id === msgId ? { ...m, content: "Explanation unavailable.", streaming: false } : m
      ));
    } finally {
      setStreaming(false);
    }
  }, [ollamaOk]);

  const handleSend = async () => {
    const q = input.trim();
    if (!q || loading) return;
    setInput("");

    const msgType = classifyMessage(q, resolvedIndustry, rankings);
    chatHistoryRef.current.push({ role: "user", content: q });
    addMsg("user", q);

    if (msgType === "chat") {
      // Conversational — no ranking re-run, no map blank
      await streamChat(q);
      return;
    }

    if (msgType === "weight_adjust") {
      addMsg("assistant", "Adjusting weights and recalculating rankings...", { loading: true });
      const opts = { silent: true };
      if (resolvedIndustry) opts.industryKeyOverride = resolvedIndustry.key;
      const result = await onQuery(q, opts);
      setMessages(prev => prev.filter(m => !m.meta?.loading));
      if (result?.type === "ok") {
        const d = result.data;
        const modKeys = Object.keys(d.weight_modifiers_applied || {});
        const signalCount = onWeightSignal ? onWeightSignal(modKeys, resolvedIndustry?.key) : 0;
        let summary = `Weights adjusted${modKeys.length ? ` (${modKeys.join(", ")})` : ""}. **${d.rankings?.[0]?.district_name}** still leads with ${d.rankings?.[0]?.score?.toFixed(1)} pts.`;
        if (signalCount >= 3) {
          summary += ` Preference learned — rankings will adapt to your priorities.`;
        } else if (signalCount > 0) {
          summary += ` [Signal ${signalCount}/3 recorded for adaptive learning.]`;
        }
        addMsg("assistant", summary);
      }
      return;
    }

    // industry_query — full ranking run
    addMsg("assistant", "Analyzing your query...", { loading: true, _id: "analyzing" });
    const result = await onQuery(q);
    setMessages(prev => prev.filter(m => m._id !== "analyzing" && !m.meta?.loading));

    if (!result) return;

    if (result.type === "confirmation_required") {
      addMsg("system_confirm", result.data.message, {
        resolved: result.data.resolved_industry, query: q,
      });
      return;
    }
    if (result.type === "no_match") {
      addMsg("assistant", `[!] Could not classify "${q}" into a supported industry. Try: food processing, automobile, dairy, textile, sugar, electronics.`);
      return;
    }
    if (result.type === "error") {
      addMsg("assistant", `[!] Error: ${result.error}`);
      return;
    }

    const { data } = result;
    const modKeys = Object.keys(data.weight_modifiers_applied || {});
    let summary = `Rankings for **${data.resolved_industry.display_name}** ready.`;
    if (modKeys.length) summary += ` Weights adjusted: ${modKeys.join(", ")}.`;
    if (data.rankings?.[0]) {
      summary += ` **${data.rankings[0].district_name}** leads with ${data.rankings[0].score.toFixed(1)} pts.`;
    }
    addMsg("assistant", summary);
    chatHistoryRef.current.push({ role: "assistant", content: summary });

  };

  const handleConfirm = async (query, key) => {
    setMessages(prev => prev.filter(m => m.role !== "system_confirm"));
    addMsg("user", `[Confirmed: ${key}]`);
    const result = await onQuery(query, { industryKeyOverride: key });
    if (result?.type === "ok" && result.data.rankings?.[0]) {
      const top = result.data.rankings[0];
      addMsg("assistant", `Rankings confirmed for **${result.data.resolved_industry.display_name}**. **${top.district_name}** leads with ${top.score.toFixed(1)} pts.`);
    }
  };

  return (
    <div style={{
      width: "380px", flexShrink: 0,
      height: "100%", background: "var(--bg-surface)",
      borderRight: "1px solid var(--border-subtle)",
      display: "flex", flexDirection: "column", flexShrink: 0,
    }}>
      <div style={{ padding: "10px 16px 8px", borderBottom: "1px solid var(--border-subtle)", flexShrink: 0 }}>
        <span style={{ fontSize: "11px", fontWeight: 500, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-muted)" }}>
          Conversation
        </span>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "12px 16px", display: "flex", flexDirection: "column", gap: "8px" }}>
        {messages.length === 0 && (
          <div style={{ textAlign: "center", padding: "28px 12px", color: "var(--text-secondary)", lineHeight: 1.6 }}>
            <div style={{ fontSize: "20px", marginBottom: "10px" }}>◈</div>
            Welcome. Describe the type of industry or location criteria and I will rank Maharashtra's 36 districts for you.
            <div style={{ marginTop: "14px", display: "flex", flexWrap: "wrap", gap: "6px", justifyContent: "center" }}>
              {EXAMPLE_CHIPS.map(c => (
                <button key={c.label} onClick={() => { setInput(c.query); inputRef.current?.focus(); }}
                  style={{
                    background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)",
                    borderRadius: "16px", padding: "4px 10px", color: "var(--text-secondary)",
                    cursor: "pointer", fontSize: "12px", fontFamily: "inherit",
                  }}>
                  ▸ {c.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => {
          if (msg.role === "user") return <UserBubble key={i} msg={msg} />;
          if (msg.role === "system_confirm") return (
            <ConfirmBubble key={i} msg={msg}
              onConfirm={handleConfirm}
              onDismiss={() => setMessages(prev => prev.filter((_, j) => j !== i))}
            />
          );
          return <AssistantBubble key={i} msg={msg} />;
        })}
        <div ref={bottomRef} />
      </div>

      {!ollamaOk && (
        <div style={{
          margin: "0 16px 8px", padding: "8px 12px", background: "var(--bg-elevated)",
          borderLeft: "2px solid var(--accent-amber)", borderRadius: "4px",
          fontSize: "12px", color: "var(--text-secondary)", flexShrink: 0,
        }}>
          [!] AI explanations unavailable. Rankings still work.
        </div>
      )}

      <div style={{ padding: "12px 16px", borderTop: "1px solid var(--border-subtle)", flexShrink: 0 }}>
        <div style={{ display: "flex", gap: "8px" }}>
          <input
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && !e.shiftKey && handleSend()}
            placeholder={streaming ? "Generating... (type your next question)" : "Ask about a district, compare, or describe an industry..."}
            disabled={loading}
            style={{
              flex: 1, background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)",
              borderRadius: "8px", padding: "8px 12px", color: "var(--text-primary)",
              fontSize: "13px", fontFamily: "inherit", outline: "none",
              opacity: loading ? 0.5 : 1,
            }}
            onFocus={e => e.target.style.borderColor = "var(--border-active)"}
            onBlur={e => e.target.style.borderColor = "var(--border-subtle)"}
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            style={{
              background: streaming ? "var(--accent-teal)" : "var(--accent-blue)",
              color: "#0B1120", border: "none", borderRadius: "8px",
              padding: "8px 14px", cursor: "pointer", fontWeight: 600,
              fontSize: "13px", fontFamily: "inherit",
              opacity: (loading || !input.trim()) ? 0.5 : 1,
              transition: "background 0.2s",
            }}
          >
            {streaming ? "⟳" : "Send →"}
          </button>
        </div>
        {streaming && (
          <div style={{ marginTop: "4px", fontSize: "10px", color: "var(--text-muted)" }}>
            Generating response — you can keep typing
          </div>
        )}
      </div>
    </div>
  );
}

function UserBubble({ msg }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-end" }}>
      <div style={{
        background: "var(--bg-elevated)", borderLeft: "2px solid var(--accent-blue)",
        borderRadius: "8px 8px 2px 8px", padding: "8px 12px",
        maxWidth: "88%", color: "var(--text-primary)", lineHeight: 1.5, fontSize: "13px",
      }}>
        {msg.content}
      </div>
    </div>
  );
}

function renderMarkdown(text) {
  const lines = text.split("\n");
  const result = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // Detect start of markdown table
    if (line.trim().startsWith("|") && i + 1 < lines.length && /^\|[-| :]+\|/.test(lines[i + 1]?.trim())) {
      const headerCells = line.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      i += 2; // skip header + separator
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(lines[i].trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim()));
        i++;
      }
      const tableStyle = {
        width: "100%", borderCollapse: "collapse", fontSize: "11px",
        background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)",
        borderRadius: "4px", marginBottom: "4px", fontFamily: "JetBrains Mono, monospace",
      };
      const thStyle = {
        padding: "4px 8px", textAlign: "left", color: "var(--text-secondary)",
        borderBottom: "1px solid var(--border-subtle)", fontWeight: 600, fontSize: "10px",
      };
      const tdStyle = { padding: "3px 8px", color: "var(--text-primary)", borderBottom: "1px solid var(--border-subtle)" };
      result.push(
        <table key={`tbl-${i}`} style={tableStyle}>
          <thead>
            <tr>{headerCells.map((h, j) => <th key={j} style={thStyle}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>{row.map((cell, ci) => <td key={ci} style={tdStyle}>{cell}</td>)}</tr>
            ))}
          </tbody>
        </table>
      );
    } else {
      const html = line.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
      result.push(<span key={i} dangerouslySetInnerHTML={{ __html: html + (i < lines.length - 1 ? "<br/>" : "") }} />);
      i++;
    }
  }
  return result;
}

function AssistantBubble({ msg }) {
  return (
    <div style={{ display: "flex", justifyContent: "flex-start" }}>
      <div style={{
        background: "var(--bg-elevated)",
        borderLeft: `2px solid ${msg.meta?.loading ? "var(--border-subtle)" : "var(--accent-teal)"}`,
        borderRadius: "8px 8px 8px 2px", padding: "8px 12px", maxWidth: "100%",
        color: msg.meta?.loading ? "var(--text-muted)" : "var(--text-primary)",
        lineHeight: 1.65, fontSize: "13px",
      }}>
        {renderMarkdown(msg.content)}
        {msg.streaming && <span className="cursor-blink" style={{ color: "var(--accent-teal)", marginLeft: "1px" }}>▊</span>}
      </div>
    </div>
  );
}

function ConfirmBubble({ msg, onConfirm, onDismiss }) {
  return (
    <div style={{
      background: "var(--bg-elevated)", borderLeft: "2px solid var(--accent-amber)",
      borderRadius: "8px", padding: "10px 12px", lineHeight: 1.6, fontSize: "13px",
    }}>
      <div style={{ color: "var(--text-primary)", marginBottom: "8px" }}>[!] {msg.content}</div>
      <div style={{ display: "flex", gap: "8px" }}>
        <button onClick={() => onConfirm(msg.meta.query, msg.meta.resolved.key)} style={{
          background: "var(--accent-blue)", color: "#0B1120", border: "none",
          borderRadius: "4px", padding: "4px 10px", cursor: "pointer",
          fontSize: "12px", fontFamily: "inherit", fontWeight: 600,
        }}>Confirm</button>
        <button onClick={onDismiss} style={{
          background: "var(--bg-surface)", color: "var(--text-secondary)",
          border: "1px solid var(--border-subtle)", borderRadius: "4px",
          padding: "4px 10px", cursor: "pointer", fontSize: "12px", fontFamily: "inherit",
        }}>Dismiss</button>
      </div>
    </div>
  );
}
