"""Profiles: listing for all kinds, CRUD for user profiles (spec §11) and the plain-language
draft builder (§9.1): one sentence → starting preset + slider positions."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid

import numpy as np

from . import config, db, intent, nlp, scoring, templates
from . import modifiers as mod
from .embeddings import INDEX

SEMANTIC_MIN = 0.6        # §9.1 step 3: cosine to a factor's exemplar document
SEMANTIC_MIN_WORDS = 2    # a clause needs this many content words before it is embedded


class ProfileForbidden(PermissionError):
    """Preset profiles are read-only."""


def _row(r) -> dict:
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "rationale": r["rationale"],
            "weights": json.loads(r["weights"]), "constraints": json.loads(r["constraints"]),
            "provenance": json.loads(r["provenance"] or "{}"), "base_profile_id": r["base_profile_id"],
            "description": r["description"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


def list_all() -> dict:
    with db.connect() as con:
        rows = [_row(r) for r in con.execute("SELECT * FROM profile ORDER BY kind, rowid")]
    return {kind: [p for p in rows if p["kind"] == kind] for kind in ("preset", "user")}


def get(pid: str) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT * FROM profile WHERE id = ?", (pid,)).fetchone()
    return _row(r) if r else None


def _new_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:32] or "profile"
    return f"user:{slug}_{uuid.uuid4().hex[:6]}"


def create(name: str, weights: dict, constraints: dict | None = None, rationale: str = "",
           description: str | None = None, base_profile_id: str | None = None, provenance: dict | None = None) -> dict:
    w = scoring.normalize_weights(weights)
    c = scoring.normalize_constraints(constraints)
    pid = _new_id(name)
    with db.connect() as con:
        con.execute(
            "INSERT INTO profile (id, kind, name, rationale, weights, constraints, provenance, base_profile_id, description) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, "user", name.strip(), rationale, json.dumps(w), json.dumps(c), json.dumps(provenance or {}),
             base_profile_id, description))
    return get(pid)


def update(pid: str, patch: dict) -> dict | None:
    cur = get(pid)
    if cur is None:
        return None
    if cur["kind"] != "user":
        raise ProfileForbidden(pid)
    new = {**cur, **{k: v for k, v in patch.items() if v is not None}}
    w = scoring.normalize_weights(new["weights"])
    c = scoring.normalize_constraints(new["constraints"])
    with db.connect() as con:
        con.execute(
            "UPDATE profile SET name=?, rationale=?, weights=?, constraints=?, provenance=?, base_profile_id=?, "
            "description=?, updated_at=datetime('now') WHERE id=?",
            (new["name"].strip(), new["rationale"], json.dumps(w), json.dumps(c), json.dumps(new["provenance"] or {}),
             new["base_profile_id"], new["description"], pid))
    return get(pid)


def delete(pid: str) -> bool:
    cur = get(pid)
    if cur is None:
        return False
    if cur["kind"] != "user":
        raise ProfileForbidden(pid)
    with db.connect() as con:
        con.execute("DELETE FROM profile WHERE id = ?", (pid,))
    return True


# ---------- rationale (generated, never typed) ----------

# Grouped phrases keep the grammar right for plural factor names ("skilled workers").
RATIONALE_HEAD = {"critical": "most weight on", "high": "more weight on", "low": "less weight on", "zero": "ignores"}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def rationale_for(base_id: str | None, weights: dict, base_weights: dict | None = None,
                  modifiers: dict | None = None) -> str:
    """'Based on Pharmaceuticals; more weight on water supply and skilled workers; ignores cheap land.'
    From the modifiers when there are any, else from what moved most against the base preset."""
    base = get(base_id) if base_id else None
    head = f"Based on {base['name']}" if base else "Custom priorities"
    bw = base_weights or (scoring.normalize_weights(base["weights"]) if base else None)
    groups: dict[str, list[str]] = {k: [] for k in RATIONALE_HEAD}
    if modifiers:
        for f, lab in modifiers.items():
            g = "zero" if lab == "exclude" or (lab == "low" and weights.get(f, 0) == 0) else lab
            groups[g].append(templates.plain_lc(f))
    elif bw:
        moved = sorted(((k, weights.get(k, 0) - bw.get(k, 0)) for k in weights), key=lambda kv: -abs(kv[1]))
        for k, d in moved[:3]:
            if abs(d) >= 0.02:
                groups["zero" if weights[k] == 0 else "high" if d > 0 else "low"].append(templates.plain_lc(k))
    else:
        groups["critical"].append(templates.plain_lc(max(weights, key=weights.get)))
    bits = [f"{RATIONALE_HEAD[g]} {_join(fs)}" for g, fs in groups.items() if fs]
    return "; ".join([head] + bits) + "."


# ---------- draft builder (§9.1) ----------

class _FieldIndex:
    """Per-factor exemplar documents (registry synonyms + data/field_exemplars.json), embedded once
    through the shared embedding cache. One row per phrase; a factor's similarity is its best row."""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.matrix: np.ndarray | None = None
        self._lock = threading.Lock()

    def ready(self) -> bool:
        if self.matrix is not None:
            return True
        if not INDEX.ready:
            return False
        with self._lock:
            if self.matrix is None:
                ex = json.loads((config.DATA_DIR / "field_exemplars.json").read_text(encoding="utf-8"))["fields"]
                syn = nlp.field_synonyms()
                keys, texts = [], []
                for f in db.fields():
                    for t in dict.fromkeys([*ex.get(f["key"], []), *f.get("synonyms", []), *syn.get(f["key"], [])]):
                        keys.append(f["key"])
                        texts.append(t)
                try:
                    m = INDEX.embed_documents(texts)
                except Exception:  # noqa: BLE001 - Ollama down: the builder runs on rules alone
                    return False
                self.keys, self.matrix = keys, m / np.linalg.norm(m, axis=1, keepdims=True)
        return True

    def best(self, text: str) -> tuple[str, float] | None:
        if not self.ready():
            return None
        q = INDEX.embed_query(text, timeout=1.5)
        if q is None:
            return None
        per: dict[str, float] = {}
        for k, v in zip(self.keys, self.matrix @ q):
            per[k] = max(per.get(k, -1.0), float(v))
        k = max(per, key=per.get)
        return k, per[k]


FIELD_INDEX = _FieldIndex()


def _clauses(tokens: list[str]) -> list[list[int]]:
    """Token positions per clause (clause splitters and , ; . end a clause)."""
    splitters = set(nlp.field_lexicon()["clause_splitters"]) | nlp.SPLIT_PUNCT
    out, cur = [], []
    for i, t in enumerate(tokens):
        if t in splitters:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(i)
    if cur:
        out.append(cur)
    return out


def _semantic(tokens: list[str], terms: list[nlp.Match], ruled: set[str]) -> tuple[dict[str, str], list[dict]]:
    """§9.1 step 3: clauses with no rule hit (no factor word) are embedded and compared with the
    factor exemplars. A non-negated clause at cosine ≥ SEMANTIC_MIN raises that factor to high."""
    field_pos = {j for m in terms if m.kind == "field" for j in range(m.start, m.end)}
    named = {j for m in terms if m.kind in ("industry", "unsupported", "generic") for j in range(m.start, m.end)}
    negators = set(nlp.field_lexicon()["negators"])
    found: dict[str, str] = {}
    trace = []
    for cl in _clauses(tokens):
        if any(j in field_pos for j in cl):
            continue  # the rules already read this clause
        words = [tokens[j] for j in cl if j not in named and tokens[j] not in nlp.STOPWORDS
                 and tokens[j] not in intent.INDUSTRY_VERBS and not tokens[j].isdigit()]
        if len(words) < SEMANTIC_MIN_WORDS or any(tokens[j] in negators for j in cl):
            continue
        hit = FIELD_INDEX.best(" ".join(tokens[j] for j in cl))
        if hit is None:
            break  # embeddings unavailable
        key, sim = hit
        trace.append({"clause": " ".join(tokens[j] for j in cl), "field": key, "sim": round(sim, 3)})
        if sim >= SEMANTIC_MIN and key not in ruled and key not in found:
            found[key] = "high"
    return found, trace


def resolve_preset(text: str, tokens: list[str], terms: list[nlp.Match], mentioned: bool) -> tuple[str, float, str]:
    """Industry → starting preset (§9.1 step 1), with the router's rule for when the embedding tier
    may speak: a bare generic word, or an unconfident guess next to factor words, is no industry."""
    has_generic = any(m.kind == "generic" for m in terms)
    has_verb = any(t in intent.INDUSTRY_VERBS for t in tokens)
    r = intent.resolve(text, tokens, terms, allow_embedding=has_generic or has_verb or not mentioned)
    if r.ok and r.tier != "generic" and r.profile_id and r.profile_id.startswith("preset:"):
        return r.profile_id, float(r.confidence), r.tier or "lexicon"
    return intent.GENERAL, 0.0, "default"


def draft(text: str, base_preset_id: str | None = None) -> dict:
    """§9.1: text → {base_preset_id, preset_confidence, modifiers, weights, preview_top5, …}. Weights
    come from modifiers.apply, the same function chat uses, so builder and chat always agree."""
    t0 = time.perf_counter()
    tokens = nlp.tokenize(text)
    terms = nlp.term_matcher().find(tokens)
    # Factor words in a description are needs: "bulk cargo by rail and sea" means rail and port matter.
    parsed = mod.parse(text, tokens, terms, default_label="high")
    rules = parsed.as_map()
    auto_id, conf, tier = resolve_preset(text, tokens, terms, bool(parsed.mentioned))
    base_id = base_preset_id or auto_id
    base = get(base_id)
    if base is None:
        raise KeyError(base_id)
    sem, trace = _semantic(tokens, terms, set(rules) | set(parsed.uncertain))
    modifiers = {**sem, **rules}
    constraints = scoring.normalize_constraints(base["constraints"])
    base_w = scoring.normalize_weights(base["weights"])  # presets are stored sparse; the 0.05 floor needs every key
    res = scoring.rank(mod.apply(base_w, modifiers), constraints, with_stability=False)
    top5 = [{"district_id": e["district_id"], "name": e["name"]}
            for e in sorted((e for e in res["ranking"] if e["eligible"]), key=lambda e: e["rank"])[:5]]
    return {"base_preset_id": base_id, "auto_preset_id": auto_id, "preset_confidence": round(conf, 3),
            "preset_tier": tier, "modifiers": modifiers, "base_weights": base_w, "weights": res["weights"],
            "constraints": constraints,
            "rationale_draft": rationale_for(base_id, res["weights"], base_w, modifiers),
            "preview_top5": top5, "semantic": trace, "uncertain": parsed.uncertain,
            "suggested_name": f"{base['name']} (custom)", "ms": round((time.perf_counter() - t0) * 1000, 1)}
