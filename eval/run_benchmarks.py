"""Benchmark harness (spec §14.1).

Usage: python eval/run_benchmarks.py [--phase N] [--llm]

Runs against the in-process app (FastAPI TestClient). Prints a table, writes
eval/results/<timestamp>.json and appends a row per gate to STAGE_LOG.md.
Phases are cumulative: --phase 1 runs the P0 and P1 gates.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
import subprocess
import sys
import itertools
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import numpy as np  # noqa: E402

from backend import config, db, scoring  # noqa: E402
from backend.main import app  # noqa: E402

RESULTS = ROOT / "eval" / "results"
STAGE_LOG = ROOT / "STAGE_LOG.md"


class Gate:
    def __init__(self, phase: int, name: str, value, target: str, passed: bool, baseline: str = "—"):
        self.phase, self.name, self.value, self.target, self.passed, self.baseline = phase, name, value, target, passed, baseline

    def row(self) -> dict:
        return {"phase": self.phase, "metric": self.name, "value": self.value, "target": self.target,
                "passed": self.passed, "baseline": self.baseline}


def pctl(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


# ---------- P0 ----------

def phase0(client: TestClient) -> list[Gate]:
    gates = []
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ROOT,
                       capture_output=True, text=True)
    summary = (r.stdout.strip().splitlines() or ["?"])[-1]
    gates.append(Gate(0, "pytest", summary, "all green", r.returncode == 0))

    client.get("/api/health")
    ts = []
    for _ in range(100):
        t = time.perf_counter()
        body = client.get("/api/health").json()
        ts.append((time.perf_counter() - t) * 1000)
    p50 = statistics.median(ts)
    gates.append(Gate(0, "/health p50 (ms)", round(p50, 2), "< 20", p50 < 20, "~2000"))
    url = config.load()["ollama"]["base_url"]
    gates.append(Gate(0, "Ollama reachable via 127.0.0.1", f"{body['ollama']['reachable']} ({url})", "true",
                      body["ollama"]["reachable"] and url.startswith("http://127.0.0.1")))
    model = body["ollama"]["narration_model"]
    gates.append(Gate(0, f"narration model pulled ({model})", body["ollama"]["model_present"], "true",
                      body["ollama"]["model_present"]))
    return gates


# ---------- P1 ----------

def phase1(client: TestClient) -> list[Gate]:
    gates = []
    con = sqlite3.connect(config.path("db"))
    n = con.execute("SELECT COUNT(*) FROM district").fetchone()[0]
    nv = con.execute("SELECT COUNT(*) FROM district_value").fetchone()[0]
    gates.append(Gate(1, "districts seeded", n, "36", n == 36 and nv == 36 * 13))
    lo, hi = con.execute("SELECT MIN(norm), MAX(norm) FROM district_value").fetchone()
    gates.append(Gate(1, "norm range", f"[{lo:.3f}, {hi:.3f}]", "within [0,1]", 0 <= lo and hi <= 1))

    fields = db.fields()
    ok = len(fields) == 13 and all(f.get("status") in {"Sourced", "Derived", "Estimated"} for f in fields)
    counts = {s: sum(f["status"] == s for f in fields) for s in ("Sourced", "Derived", "Estimated")}
    gates.append(Gate(1, "registry fields with status", f"{len(fields)} {counts}", "13, all with status", ok))

    src = con.execute("SELECT value FROM meta WHERE key='centroid_source'").fetchone()[0]
    gates.append(Gate(1, "port distance centroid source", src, "polygon", src == "polygon", "HQ town"))

    norm = dict(con.execute("SELECT district_id, norm FROM district_value WHERE field='port'").fetchall())
    raw = dict(con.execute("SELECT district_id, raw FROM district_value WHERE field='port'").fetchall())
    gates.append(Gate(1, "Raigad port norm ≥ Ratnagiri",
                      f"{norm[4]:.3f} ({raw[4]} km) vs {norm[5]:.3f} ({raw[5]} km)", "Raigad ≥ Ratnagiri",
                      norm[4] >= norm[5], "Ratnagiri #1 (1 km)"))
    con.close()

    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_data.py"],
                       cwd=ROOT, capture_output=True, text=True)
    gates.append(Gate(1, "transform/normalization unit tests", (r.stdout.strip().splitlines() or ["?"])[-1],
                      "all green", r.returncode == 0))

    m = json.loads(config.path("map_json").read_text(encoding="utf-8"))
    ids = [d["id"] for d in m["districts"]]
    gates.append(Gate(1, "map districts (unique ids)", f"{len(ids)} ({len(set(ids))} unique)", "36 unique",
                      sorted(ids) == list(range(1, 37))))
    with open(config.path("centroids_csv"), newline="") as f:
        nc = sum(1 for _ in csv.DictReader(f))
    gates.append(Gate(1, "centroids.csv rows", nc, "36", nc == 36))
    return gates


# ---------- P2 ----------

def phase2(client: TestClient) -> list[Gate]:
    gates = []
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "tests/test_scoring.py", "tests/test_api_rank.py"], cwd=ROOT, capture_output=True, text=True)
    gates.append(Gate(2, "scoring/API unit tests", (r.stdout.strip().splitlines() or ["?"])[-1], "all green",
                      r.returncode == 0))

    bad = ['{"weights": {"power": -1}}', '{"weights": {"power": NaN}}', '{"weights": {"power": 0, "water": 0}}',
           '{"weights": {"power": Infinity}}', '{"weights": {"bogus": 1}}', '{"weights": {}}']
    codes = [client.post("/api/rank", content=b, headers={"content-type": "application/json"}).status_code for b in bad]
    n422 = sum(c == 422 for c in codes)
    gates.append(Gate(2, "invalid weights → 422 (neg/NaN/zero/inf/unknown/empty)", f"{n422}/{len(bad)}",
                      "all 422", n422 == len(bad), "negative accepted (375/100)"))

    with db.connect() as con:
        std = [row[0] for row in con.execute("SELECT id FROM profile WHERE kind='preset' ORDER BY rowid")]
    profiles = [scoring.load_profile(pid) for pid in std]
    rng = np.random.default_rng(0)
    bodies = [{"weights": p["weights"], "constraints": p["constraints"]} for p in profiles]
    bodies += [{"weights": dict(zip(db.field_keys(), rng.random(13).tolist()))} for _ in range(20)]
    sizes = [len(client.post("/api/rank", json=b).json()["ranking"]) for b in bodies]
    gates.append(Gate(2, "responses with 36 entries", f"{sum(n == 36 for n in sizes)}/{len(sizes)}", "all",
                      all(n == 36 for n in sizes)))

    metro_ok = 0
    for b in bodies[:len(profiles)]:
        rk = client.post("/api/rank", json=b).json()["ranking"]
        metro_ok += all(not e["eligible"] and e["rank"] is None for e in rk if e["district_id"] in (1, 2))
    gates.append(Gate(2, "metro ineligible (all presets)", f"{metro_ok}/{len(profiles)}",
                      f"{len(profiles)}/{len(profiles)}", len(profiles) == 12 and metro_ok == len(profiles),
                      "Mumbai #1 auto/electronics"))

    ts = []
    for p in profiles:
        res = scoring.rank(p["weights"], p["constraints"], with_stability=False)
        rows = np.array([scoring.dataset().row(e["district_id"]) for e in res["ranking"] if e["eligible"]])
        w = scoring.weight_vector(res["weights"])
        for _ in range(20):
            t = time.perf_counter()
            scoring.stability(w, rows, np.arange(1, len(rows) + 1))
            ts.append((time.perf_counter() - t) * 1000)
    gates.append(Gate(2, f"stability p95 (ms, N={len(ts)})", round(pctl(ts, 95), 2), "< 30", pctl(ts, 95) < 30))

    ts = []
    for _ in range(10):
        for b in bodies[:len(profiles)]:
            t = time.perf_counter()
            client.post("/api/rank", json=b)
            ts.append((time.perf_counter() - t) * 1000)
    gates.append(Gate(2, "/rank p95 (ms)", round(pctl(ts, 95), 2), "< 50", pctl(ts, 95) < 50))

    checked = violations = 0
    for pid in std:
        for did in range(1, 37):
            body = client.get(f"/api/district/{did}", params={"profile_id": pid}).json()
            for s in body["strengths"]:
                checked += 1
                violations += s["norm"] < 0.6
    gates.append(Gate(2, "strengths with norm < 0.6", f"{violations} of {checked} ({len(std)} presets × 36)", "0",
                      violations == 0 and checked > 0, "bottom-quartile literacy listed"))
    return gates


# ---------- P3 ----------

SPEC = ROOT / "i-want-you-to-floofy-river.md"
SPEC_FIELD_ALIASES = {"road": "road_density"}
TUNE_TOL = 0.05
FACE_VALIDITY = "plastics_rubber"


def spec_start_weights() -> dict[str, dict[str, float]]:
    """§6.1 starting weights, read from the spec table so the ±0.05 gate checks the real plan."""
    text = SPEC.read_text(encoding="utf-8")
    sec = text[text.index("### 6.1"):text.index("### 6.2")]
    out = {}
    for line in sec.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 7 and cells[0].isdigit():
            w = {}
            for part in cells[4].split(","):
                name, val = part.strip().rsplit(" ", 1)
                w[SPEC_FIELD_ALIASES.get(name, name)] = float(val)
            out[cells[1]] = w
    return out


def ranked_names(profile: dict) -> list[str]:
    """Eligible district names in rank order (scoring.rank, so the §5.2 tie-break applies)."""
    res = scoring.rank(profile["weights"], profile["constraints"], with_stability=False)
    return [e["name"] for e in sorted((e for e in res["ranking"] if e["eligible"]), key=lambda e: e["rank"])]


def precision_at_5(order: list[str], gt: list[str]) -> float:
    return len(set(order[:5]) & set(gt)) / 5


def hit_at_10(order: list[str], gt: list[str]) -> float:
    return len(set(order[:10]) & set(gt)) / min(10, len(gt))


def spearman(a: list[str], b: list[str]) -> float:
    """Spearman ρ over the districts both orders contain (ranks re-derived on that common set)."""
    common = [n for n in a if n in set(b)]
    ra = {n: i for i, n in enumerate(common)}
    rb = {n: i for i, n in enumerate(n for n in b if n in ra)}
    x = np.array([ra[n] for n in common], float)
    y = np.array([rb[n] for n in common], float)
    return float(np.corrcoef(x, y)[0, 1])


def presets() -> dict[str, dict]:
    with db.connect() as con:
        pids = [row[0] for row in con.execute("SELECT id FROM profile WHERE kind='preset' ORDER BY rowid")]
    return {pid.split(":", 1)[1]: scoring.load_profile(pid) for pid in pids}


def phase3(client: TestClient) -> list[Gate]:
    gates = []
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_profiles.py"],
                       cwd=ROOT, capture_output=True, text=True)
    gates.append(Gate(3, "profile unit tests", (r.stdout.strip().splitlines() or ["?"])[-1], "all green",
                      r.returncode == 0))

    gt = {k: v for k, v in json.loads((ROOT / "data" / "ground_truth.json").read_text(encoding="utf-8")).items()
          if not k.startswith("_")}
    pre = presets()
    start = spec_start_weights()
    gates.append(Gate(3, "presets (one list)", f"{len(pre)}: {', '.join(pre)}", "12, as in §6.1",
                      list(pre) == list(start) and len(pre) == 12))

    orders = {k: ranked_names(p) for k, p in pre.items()}
    backed = [k for k in pre if k in gt]
    p5 = {k: precision_at_5(orders[k], gt[k]) for k in backed}
    h10 = {k: hit_at_10(orders[k], gt[k]) for k in backed}
    mean_p5, mean_h10 = statistics.mean(p5.values()), statistics.mean(h10.values())
    gates.append(Gate(3, f"mean P@5 ({len(backed)} GT-backed)", round(mean_p5, 3), "≥ 0.70",
                      len(backed) == 11 and mean_p5 >= 0.70, "0.57"))
    worst = min(p5, key=p5.get)
    gates.append(Gate(3, "every P@5", f"min {p5[worst]:.2f} ({worst}); "
                      + " ".join(f"{k} {v:.1f}" for k, v in p5.items()), "each ≥ 0.60",
                      all(v >= 0.60 for v in p5.values()), "steel preset 0.0"))
    gates.append(Gate(3, f"mean hit@10 ({len(backed)} GT-backed)", round(mean_h10, 3), "≥ 0.90", mean_h10 >= 0.90))

    pairs = list(itertools.combinations(orders, 2))
    rho = statistics.mean(spearman(orders[a], orders[b]) for a, b in pairs)
    gates.append(Gate(3, f"distinctness (mean ρ over {len(pairs)} pairs)", round(rho, 4), "≤ 0.92",
                      len(pairs) == 66 and rho <= 0.92, "0.967"))
    top5 = Counter(n for o in orders.values() for n in o[:5])
    dom = max(top5.values())
    leaders = ", ".join(f"{n} {c}" for n, c in top5.most_common() if c == dom)
    # Reported, not gated: product-owner sign-off 2026-10-01 (Nashik/Pune data limitation, see STAGE_LOG).
    gates.append(Gate(3, "dominance (max top-5 appearances, reported)", f"{dom} of {len(orders)} ({leaders})",
                      "report (2026-10-01 sign-off)", True, "Mumbai #1 auto/electronics"))

    keys = db.field_keys()
    vec = {k: np.array([p["weights"].get(f, 0.0) for f in keys]) for k, p in pre.items()}
    cos = {(a, b): float(vec[a] @ vec[b] / np.linalg.norm(vec[a]) / np.linalg.norm(vec[b]))
           for a, b in itertools.combinations(vec, 2)}
    pair = max(cos, key=cos.get)
    gates.append(Gate(3, "max preset pairwise cosine", f"{cos[pair]:.3f} ({pair[0]}/{pair[1]})", "< 0.95",
                      len(pre) == 12 and cos[pair] < 0.95))

    missing = [k for k, p in pre.items() if len(p["rationale"].strip()) < 20]
    gates.append(Gate(3, "presets with a rationale", f"{len(pre) - len(missing)}/{len(pre)}", "all",
                      not missing and len(pre) == 12))

    drift = {}
    max_d = 0.0
    for k, p in pre.items():
        base = start.get(k, {})
        for f in keys:
            d = abs(p["weights"].get(f, 0.0) - base.get(f, 0.0))
            max_d = max(max_d, d)
            if d > TUNE_TOL + 1e-9:
                drift[f"{k}.{f}"] = round(d, 3)
    gates.append(Gate(3, "tuning within ±0.05 of §6.1", f"max |Δ| {max_d:.3f}" + (f" over: {drift}" if drift else ""),
                      "≤ 0.05 per field", not drift))

    log = STAGE_LOG.read_text(encoding="utf-8") if STAGE_LOG.exists() else ""
    ok = "Plastics & Rubber face-validity review" in log and FACE_VALIDITY not in gt
    gates.append(Gate(3, "Plastics & Rubber face-validity review logged", "yes" if ok else "no", "logged", ok))

    unsupported = json.loads((ROOT / "data" / "lexicon_industry.json").read_text(encoding="utf-8"))["unsupported"]
    bound = [k for k, p in pre.items()
             if any(re.search(rf"\b{re.escape(t)}", f"{k.replace('_', ' ')} {p['name']}".lower()) for t in unsupported)]
    gates.append(Gate(3, "resource-bound presets", len(bound) if not bound else ", ".join(bound), "0",
                      not bound, "steel preset"))
    return gates


# ---------- P4 ----------

def phase4(client: TestClient) -> list[Gate]:
    from backend import intent
    from backend.embeddings import INDEX
    from eval import intent_eval as ie

    gates = []
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_language.py"],
                       cwd=ROOT, capture_output=True, text=True)
    gates.append(Gate(4, "language/session unit tests", (r.stdout.strip().splitlines() or ["?"])[-1], "all green",
                      r.returncode == 0))

    ready = INDEX.wait_ready(180)
    tau, delta = intent.thresholds()
    gates.append(Gate(4, "embedding tier ready (calibrated τ/δ)", f"{ready} ({len(INDEX.labels)} exemplars, τ={tau}, δ={delta})",
                      "true", ready))

    rows = ie.load_jsonl(ie.CORPUS)
    with INDEX._lock:
        INDEX._qcache.clear()  # measure latency with cold query embeddings
    res = ie.run_corpus(client, rows)
    errors = [x for x in res if "error" in x]
    ok = [x for x in res if "error" not in x]
    test = [x for x in ok if x["row"]["split"] == "test"]
    acc = ie.accuracy(test)
    gates.append(Gate(4, f"intent accuracy (held-out {len(test)} of {len(rows)} rows)",
                      f"{acc:.3f} (calib split {ie.accuracy([x for x in ok if x['row']['split'] == 'calib']):.3f})",
                      "≥ 0.92", acc >= 0.92 and not errors))
    f1 = ie.modifier_f1(ok)
    gates.append(Gate(4, "modifier micro-F1", f"{f1['f1']:.3f} (tp {f1['tp']}, fp {f1['fp']}, fn {f1['fn']})", "≥ 0.92",
                      f1["f1"] >= 0.92))
    n_ok, n = ie.negation_pass(ok)
    gates.append(Gate(4, "negation pass", f"{n_ok}/{n}", "100%", n > 0 and n_ok == n))
    fp = ie.adversarial_fp(ok)
    n_adv = sum("adversarial" in x["row"]["tags"] for x in ok)
    gates.append(Gate(4, f"adversarial FP ({n_adv} rows)", len(fp) if not fp else f"{len(fp)}: {fp}", "0", not fp,
                      "carpets→auto, important→port"))
    c_ok, c_n = ie.clarify_recall(ok)
    gates.append(Gate(4, "clarify recall", f"{c_ok}/{c_n}", "100%", c_n > 0 and c_ok == c_n, "unknown → silent general"))

    convs = ie.load_jsonl(ie.CONVERSATIONS)
    outcomes = [(cv["id"], *ie.run_conversation(client, cv)) for cv in convs]
    failed = [f"#{i}: {errs[0]}" for i, passed, errs in outcomes if not passed]
    gates.append(Gate(4, "conversation scripts", f"{len(convs) - len(failed)}/{len(convs)}" + (f" {failed}" if failed else ""),
                      "11/11", len(convs) == 11 and not failed, "context lost"))

    rule = [x["server_ms"] for x in ok if x["tier"] != "embedding"]
    emb = [x["server_ms"] for x in ok if x["tier"] == "embedding"]
    gates.append(Gate(4, f"router p95, rule path (ms, n={len(rule)})", round(pctl(rule, 95), 2), "< 100",
                      pctl(rule, 95) < 100))
    gates.append(Gate(4, f"router p95, embedding tier (ms, n={len(emb)}, cold)", round(pctl(emb, 95), 2) if emb else "n/a",
                      "< 400", bool(emb) and pctl(emb, 95) < 400))

    # Extra unseen-phrasing sets written after the first P4 run (see STAGE_LOG for blind first-run scores).
    extra = {"dev (tuning)": ie.DEV, "holdout 2": ie.HOLDOUT2, "holdout 3": ie.HOLDOUT3, "holdout 4": ie.HOLDOUT4}
    pooled, all_res = [], list(ok)
    for name, path in extra.items():
        rs = [x for x in ie.run_corpus(client, ie.load_jsonl(path)) if "error" not in x]
        all_res += rs
        if name != "dev (tuning)":
            pooled += rs
        gates.append(Gate(4, f"intent accuracy, {name} ({len(rs)} rows)", f"{ie.accuracy(rs):.3f}", "report", True))
    pacc = ie.accuracy(pooled)
    gates.append(Gate(4, f"intent accuracy, holdouts 2–4 pooled ({len(pooled)} rows)", f"{pacc:.3f}", "≥ 0.92",
                      pacc >= 0.92))
    silent = ie.silent_switches(all_res)
    gates.append(Gate(4, f"silent preset switches ({len(all_res)} rows, all sets)", len(silent) if not silent
                      else f"{len(silent)}: {silent[:3]}", "0 (every change via Apply)", not silent,
                      "industry switched on any mention"))
    wrong = ie.wrong_direction(all_res)
    gates.append(Gate(4, "wrong-direction modifiers (all sets)", len(wrong) if not wrong else f"{len(wrong)}: {wrong}", "0",
                      not wrong))

    INDEX.enabled = False
    try:
        off = ie.run_corpus(client, rows)
    finally:
        INDEX.enabled = True
    off_err = [x for x in off if "error" in x]
    off_test = [x for x in off if "error" not in x and x["row"]["split"] == "test"]
    off_acc = ie.accuracy(off_test)
    gates.append(Gate(4, "embeddings offline: accuracy, server errors", f"{off_acc:.3f}, {len(off_err)} errors",
                      "≥ 0.85, 0", off_acc >= 0.85 and not off_err))
    return gates

# ---------- P5 ----------

def scope_rates(path: Path) -> tuple[int, int, int, int]:
    from backend import scope
    tp = fn = tn = fp = 0
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        hit = scope.check(r["text"]).out_of_scope
        if r["expect"] == "refusal":
            tp, fn = tp + hit, fn + (not hit)
        else:
            tn, fp = tn + (not hit), fp + hit
    return tp, tp + fn, tn, tn + fp


COOLDOWN_S = 60


def phase5(client: TestClient, llm: bool = False) -> list[Gate]:
    from backend import scope
    from eval import intent_eval as ie
    from eval import narration_eval as ne

    gates = []
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_narration.py"],
                       cwd=ROOT, capture_output=True, text=True)
    gates.append(Gate(5, "narration unit tests", (r.stdout.strip().splitlines() or ["?"])[-1], "all green",
                      r.returncode == 0))

    # Scope guard: tuned on scope_dev, measured blind on scope_holdout; controls = every P4 corpus/dialog turn.
    for name, path in (("dev", ROOT / "eval" / "scope_dev.jsonl"), ("holdout", ROOT / "eval" / "scope_holdout.jsonl")):
        tp, n_out, tn, n_in = scope_rates(path)
        gates.append(Gate(5, f"out-of-scope refused, {name} set", f"{tp}/{n_out} refused, {tn}/{n_in} in-scope kept",
                          "100%, 0 false refusals", tp == n_out and tn == n_in, "GDP/poem/weather → ranked or adjusted"))
    controls = []
    for path in [ie.CORPUS, ie.DEV, ie.HOLDOUT2, ie.HOLDOUT3, ie.HOLDOUT4]:
        controls += [row["text"] for row in ie.load_jsonl(path)]
    for cv in ie.load_jsonl(ie.CONVERSATIONS):
        controls += [st["say"] for st in cv["steps"] if st.get("say")]
    false_ref = [t for t in controls if scope.check(t).out_of_scope]
    gates.append(Gate(5, f"false refusals on P4 corpora + dialogs ({len(controls)} turns)",
                      len(false_ref) if not false_ref else f"{len(false_ref)}: {false_ref[:3]}", "0", not false_ref))

    rows = ne.load_jsonl(ne.SUITE)
    off = ne.run_offline(client, client.app, rows)
    gates.append(Gate(5, "Ollama offline: templated replies, server errors",
                      f"{off['templated']}/{off['turns']} templated, {len(off['server_errors'])} errors, "
                      f"narrate → {off['forced_narrate_events']}/{off['dead_port_events']}, dialogs "
                      f"{off['conversations'][0]}/{off['conversations'][1]}",
                      "100%, 0", off["templated"] == off["turns"] and not off["server_errors"]
                      and off["forced_narrate_events"] == ["error"] and off["dead_port_events"] == ["error"]
                      and off["conversations"][0] == off["conversations"][1]))

    if not llm:
        gates.append(Gate(5, "live-LLM gates (groundedness, refusal, latency)", "not run", "run with --llm", False))
        return gates

    ready = ne.wait_warm(client.app)
    # The earlier phases keep all cores busy for ~20 min; on the fanless-turbo reference laptop that
    # alone slows prompt evaluation ~50%. Let it idle like a machine a person sits down at.
    time.sleep(COOLDOWN_S)
    res = ne.run_live(client, rows)
    sm = ne.summarize(res)
    model = config.load()["ollama"]["narration_model"]
    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(RESULTS / f"narration-{datetime.now():%Y%m%d-%H%M%S}.jsonl", "w", encoding="utf-8") as f:
        for x in res:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    # Reported: a timeout is the designed fallback (§8.1 "AI note unavailable (timeout)"); it is counted
    # in the first-token p50 below at the timeout value rather than dropped.
    gates.append(Gate(5, f"narrated responses ({model}, warm={ready}, {COOLDOWN_S} s idle first, report)",
                      f"{sm['narrated']}/{sm['in_scope']}" + (f" timeouts {sm['timeouts']}" if sm["timeouts"] else "")
                      + (f" other errors {[e for e in sm['narration_errors'] if e not in sm['timeouts']]}"
                         if set(sm["narration_errors"]) - set(sm["timeouts"]) else ""),
                      "report", not (set(sm["narration_errors"]) - set(sm["timeouts"]))))
    gates.append(Gate(5, "fully grounded responses", f"{sm['grounded_share']:.3f}"
                      + (f" (ungrounded: {sm['ungrounded']})" if sm["ungrounded"] else ""), "≥ 0.90",
                      sm["grounded_share"] >= 0.90))
    gates.append(Gate(5, "numeric claims supported", f"{sm['numeric_supported_share']:.3f} of {sm['numbers']}",
                      "≥ 0.97", sm["numeric_supported_share"] >= 0.97, "ranked #0"))
    gates.append(Gate(5, "wrong direction claims, compare takeaways", len(sm["compare_wrong_directions"])
                      if not sm["compare_wrong_directions"] else f"{len(sm['compare_wrong_directions'])}: {sm['compare_wrong_directions']}",
                      "0", not sm["compare_wrong_directions"], "compare text contradicted its table"))
    gates.append(Gate(5, "wrong direction claims, all tasks (report)", sm["all_wrong_directions"] or 0, "report", True))
    ok, n = sm["refusal"]
    gates.append(Gate(5, "refusal (out-of-scope prompts)", f"{ok}/{n}", "6/6", n == 6 and ok == n))
    gates.append(Gate(5, f"first token p50, warm (ms, {ne.THINK_S:.0f} s between prompts, timeouts counted)",
                      sm["ttft_p50"], "< 3000",
                      sm["ttft_p50"] is not None and sm["ttft_p50"] < 3000, "28–37 s"))
    gates.append(Gate(5, "full response p90 (ms)", sm["total_p90"], "< 20000",
                      sm["total_p90"] is not None and sm["total_p90"] < 20000))
    gates.append(Gate(5, "chat-suite routing (kind as expected)", f"{30 - len(sm['kind_errors'])}/30"
                      + (f" {sm['kind_errors']}" if sm["kind_errors"] else ""), "30/30", not sm["kind_errors"]))
    return gates


_UI: dict = {}
RUN_PHASE = 0  # set by main(): P6 runs the 1–20 walkthrough once when P7 will need it too


def ui_results(to: int) -> dict:
    """One headless walkthrough run (eval/ui_runner.py) shared by the P6 and P7 gates."""
    if _UI.get("to", 0) < to:
        from eval import ui_runner
        _UI.clear()
        _UI.update(ui_runner.run(to), to=to)
    return _UI


def ui_steps(res: dict, lo: int, hi: int) -> tuple[bool, str]:
    """Pass/fail of steps lo..hi at every resolution, with the failing steps named."""
    if "error" in res:
        return False, res["error"][:200]
    parts, ok = [], True
    for r in res["runs"]:
        sel = [s for s in r["steps"] if lo <= s["step"] <= hi]
        bad = [f"{s['step']}: {s['detail'][:80]}" for s in sel if not s["pass"]]
        ok = ok and len(sel) == hi - lo + 1 and not bad
        parts.append(f"{r['viewport']} {len(sel) - len(bad)}/{hi - lo + 1}" + (f" {bad}" if bad else ""))
    return ok, "; ".join(parts)


def phase6(client: TestClient, llm: bool = False) -> list[Gate]:
    from eval import ui_runner

    gates = []
    res = ui_results(20 if RUN_PHASE >= 7 else 14)
    ok, val = ui_steps(res, 1, 14)
    gates.append(Gate(6, f"UI walkthrough steps 1–14 (headless {res.get('browser', '?')})", val,
                      "14/14 at both resolutions", ok, "industry switch ignored; reset swapped profile; popup stale"))
    runs = res.get("runs", [])
    errs = [e for r in runs for e in r.get("console", []) + r.get("errors", [])]
    gates.append(Gate(6, "console errors/warnings from app code", len(errs) if not errs else f"{len(errs)}: {errs[:2]}",
                      "0", bool(runs) and not errs))
    t = ui_runner.summarize_timing(runs) if runs else {}
    gates.append(Gate(6, f"map recolor p95 (ms, response → next frame + {ui_runner.FILL_TRANSITION_MS} ms fill, "
                         f"n={t.get('recolor_n', 0)})", t.get("recolor_p95"), "< 300",
                      bool(t.get("recolor_n")) and t["recolor_p95"] < 300))
    gates.append(Gate(6, f"slider drag → re-ranked map p95 (ms, last input → next frame, n={t.get('slider_n', 0)})",
                      t.get("slider_p95"), "< 200", bool(t.get("slider_n")) and t["slider_p95"] < 200))
    for step, name in ((11, "no numbers or input boxes on weight controls; plain labels (DOM)"),
                       (8, "popup values equal /district (DOM vs API)"),
                       (12, "open popup re-fetches on weights change (DOM vs API)"),
                       (10, "all 36 districts clickable, incl. both Mumbai")):
        ok, val = ui_steps(res, step, step)
        gates.append(Gate(6, name, "pass" if ok else val, "both resolutions", ok))
    lint = ui_runner.lint()
    gates.append(Gate(6, "no dangerouslySetInnerHTML/innerHTML (eslint: src clean, probe rejected)",
                      f"src clean={lint['src_clean']}, probe errors={lint['probe_errors']}", "clean, ≥ 2",
                      lint["src_clean"] and lint["probe_rejected"]))
    return gates


def phase7(client: TestClient, llm: bool = False) -> list[Gate]:
    from backend import clusters
    from backend.embeddings import INDEX
    from eval import intent_eval as ie
    from eval import profile_eval as pe

    gates = []
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_builder.py"],
                       cwd=ROOT, capture_output=True, text=True)
    gates.append(Gate(7, "builder/CRUD/save-from-chat/cluster tests", (r.stdout.strip().splitlines() or ["?"])[-1],
                      "all green", r.returncode == 0))

    INDEX.wait_ready(120)
    res = pe.run(client)
    bad = [x["id"] for x in res["rows"] if not x["pass"]]
    gates.append(Gate(7, f"profile corpus: preset auto-pick ({res['n']} rows, embeddings={INDEX.ready})",
                      f"{res['preset_acc']:.2f}" + (f" (wrong: {[x['id'] for x in res['rows'] if not x['preset_ok']]})"
                                                   if res["preset_acc"] < 1 else ""), "≥ 0.90", res["preset_acc"] >= 0.90))
    gates.append(Gate(7, "profile corpus: rows passing (§14.3 rule)", f"{res['pass_rate']:.2f}" + (f" (fail: {bad})" if bad else ""),
                      "≥ 0.90", res["pass_rate"] >= 0.90))
    ok_neg, n_neg = res["negated"]
    gates.append(Gate(7, "negated fields below their starting-preset value", f"{ok_neg}/{n_neg}", "100%",
                      n_neg > 0 and ok_neg == n_neg, "\"land does not matter\" gave land 64%"))
    top1 = [x for x in res["rows"] if "top1" in next(r for r in pe.load_jsonl(pe.CORPUS) if r["id"] == x["id"])]
    gates.append(Gate(7, "rows annotated top-1 with that field first (report)",
                      f"{sum(x['top1_ok'] for x in top1)}/{len(top1)} "
                      f"{[x['id'] for x in top1 if not x['top1_ok']] or ''}".strip(), "report", True))
    gates.append(Gate(7, "draft latency p95 (ms, API round trip)", res["latency_p95"], "< 600", res["latency_p95"] < 600))

    convs = {c["id"]: c for c in ie.load_jsonl(ie.CONVERSATIONS)}
    results = {i: ie.run_conversation(client, convs[i]) for i in (4, 5, 9, 10)}
    failed = {i: errs[:1] for i, (ok, errs) in results.items() if not ok}
    gates.append(Gate(7, "conversation scripts 4, 5, 9, 10", f"{4 - len(failed)}/4" + (f" {failed}" if failed else ""),
                      "4/4", not failed))

    c = clusters.compute()
    labels = [cl["label"] for cl in c["clusters"]]
    colors = {cl["color"] for cl in c["clusters"]}
    gates.append(Gate(7, "clusters: k by silhouette in [3,6], unique labels and colors",
                      f"k={c['k']} silhouette={c['silhouette']} (tried {c['silhouettes']})",
                      "k ∈ [3,6], unique", 3 <= c["k"] <= 6 and len(set(labels)) == len(labels) == len(colors),
                      "duplicate cluster labels; colors repeat for k > 4"))
    every_k = [clusters.compute(k) for k in range(3, 7)]
    gates.append(Gate(7, "clusters: unique labels and colors for every k = 3…6",
                      "pass" if all(len({x["label"] for x in r["clusters"]}) == len({x["color"] for x in r["clusters"]}) == r["k"]
                                    for r in every_k) else "fail", "all", all(
                          len({x["label"] for x in r["clusters"]}) == len({x["color"] for x in r["clusters"]}) == r["k"]
                          for r in every_k)))

    ui = ui_results(20)
    ok, val = ui_steps(ui, 15, 20)
    gates.append(Gate(7, "UI walkthrough steps 15–20 (step 20 with Ollama unreachable)", val,
                      "6/6 at both resolutions", ok))
    errs = [e for r in ui.get("runs", []) for e in r.get("console", []) + r.get("errors", [])]
    gates.append(Gate(7, "console errors/warnings, steps 1–20", len(errs) if not errs else f"{len(errs)}: {errs[:2]}",
                      "0", bool(ui.get("runs")) and not errs))
    return gates


# ---------- P8 ----------

def readme_mismatches() -> list[str]:
    """README must match the code: every API route in the table exists and vice versa; ports and model names
    from config.toml appear in it."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    documented = set()
    for line in readme.splitlines():
        m = re.match(r"\|\s*([A-Z ·]+?)\s*\|\s*(`[^|]+`)\s*\|", line)
        if not m:
            continue
        methods = [x.strip() for x in m.group(1).split("·")]
        paths = re.findall(r"`([^`]+)`", m.group(2))
        # "PUT · DELETE | /profiles/{id}" (methods share a path) or "POST · GET | /session · /session/{id}" (pairs)
        pairs = [(mt, paths[0]) for mt in methods] if len(paths) == 1 else zip(methods, paths)
        for meth, path in pairs:
            documented.add((meth, "/api" + re.sub(r"\?.*$", "", path)))
    actual = {(meth, re.sub(r"\{[^}]+\}", "{id}", r.path)) for r in app.routes if r.path.startswith("/api")
              for meth in getattr(r, "methods", set()) - {"HEAD"}}
    documented = {(m, re.sub(r"\{[^}]+\}", "{id}", p)) for m, p in documented}
    out = [f"undocumented {m} {p}" for m, p in sorted(actual - documented)]
    out += [f"documented but missing {m} {p}" for m, p in sorted(documented - actual)]
    cfg = config.load()
    for want in (str(cfg["server"]["backend_port"]), str(cfg["server"]["frontend_port"]), cfg["ollama"]["base_url"],
                 cfg["ollama"]["narration_model"], cfg["ollama"]["embedding_model"]):
        if want not in readme:
            out.append(f"README lacks {want}")
    return out


def phase8(client: TestClient, llm: bool = False, prior: list[Gate] | None = None) -> list[Gate]:
    from eval import cold_start, ui_runner

    gates = []
    prior = prior or []
    bad = [f"P{g.phase} {g.name}" for g in prior if not g.passed]
    gates.append(Gate(8, f"all P0–P7 gates still pass ({len(prior)} gates)", "all pass" if not bad else f"{len(bad)} fail: {bad[:3]}",
                      "all", bool(prior) and not bad))
    miss = readme_mismatches()
    gates.append(Gate(8, "README matches the code (API routes, ports, Ollama URL, models)", "match" if not miss else miss[:4],
                      "match", not miss, "docs contradicted the code"))
    r = client.get("/api/meta").json()
    gates.append(Gate(8, "About and §4.7 limitations served to the Data modal",
                      f"{len(r['about']['about'])} about, {len(r['about']['limitations'])} limitations", "4 limitations",
                      len(r["about"]["limitations"]) == 4 and bool(r["about"]["about"])))
    if llm:
        demo = ui_runner.run(build=True, demo=3)
        runs = demo.get("runs", [])
        detail = demo.get("error", "")[:200] or "; ".join(
            f"run {i + 1} {x.get('passed', 0)}/{x.get('total', 9)}"
            + (f" {[(st['step'], st['detail'][:60]) for st in x.get('steps', []) if not st['pass']]}"
               if x.get("passed") != 9 else "")
            + (f" console {x.get('console', [])[:1] + x.get('errors', [])[:1]}" if x.get("console") or x.get("errors") else "")
            for i, x in enumerate(runs))
        ok = len(runs) == 3 and all(x.get("passed") == x.get("total") == 9 and not x.get("console") and not x.get("errors")
                                    for x in runs)
        gates.append(Gate(8, "demo script (§16), 3 consecutive runs, live Ollama, 0 console errors", detail, "3 × 9/9", ok))
        cold = cold_start.run()
        gates.append(Gate(8, "fresh clone → usable UI, cold (npm install, seed, servers, map drawn)",
                          f"{cold.get('total_s')} s {cold.get('steps')}" if "error" not in cold else cold["error"],
                          f"< {cold_start.LIMIT_S} s", bool(cold.get("pass"))))
    return gates


def phase9(client: TestClient, llm: bool = False) -> list[Gate]:
    from eval import p9_bench
    return p9_bench.phase9(client, llm, Gate, pctl)


def phase10(client: TestClient, llm: bool = False) -> list[Gate]:
    from eval import p10_bench
    return p10_bench.phase10(client, llm, Gate, pctl)


PHASES = {0: phase0, 1: phase1, 2: phase2, 3: phase3, 4: phase4, 5: phase5, 6: phase6, 7: phase7, 8: phase8, 9: phase9,
          10: phase10}

# Headline tiles for the Data modal (§16 step 9): (label, metric-name prefix).
HEADLINE = [("Mean P@5, 11 presets", 3, "mean P@5"),
            ("Intent accuracy, held out", 4, "intent accuracy (held-out"),
            ("Modifier F1", 4, "modifier micro-F1"),
            ("Grounded AI notes", 5, "fully grounded responses"),
            ("Profile auto-pick", 7, "profile corpus: preset auto-pick"),
            ("/rank p95 (ms)", 2, "/rank p95")]


def write_summary(gates: list[Gate], stamp: str, llm: bool) -> None:
    """eval/benchmark_summary.json, read by GET /api/benchmarks (backend/benchmarks.py)."""
    def head(label: str, phase: int, prefix: str) -> dict | None:
        g = next((g for g in gates if g.phase == phase and g.name.startswith(prefix)), None)
        if g is None:
            return None
        v = str(g.value)
        return {"label": label, "value": v.split(" ")[0], "target": g.target, "pass": g.passed}

    summary = {"timestamp": stamp, "llm": llm, "passed": sum(g.passed for g in gates), "total": len(gates),
               "headline": [h for h in (head(*x) for x in HEADLINE) if h],
               "gates": [{**g.row(), "value": str(g.value)} for g in gates]}
    (ROOT / "eval" / "benchmark_summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False),
                                                         encoding="utf-8")


def append_stage_log(gates: list[Gate], stamp: str) -> None:
    new = not STAGE_LOG.exists()
    with open(STAGE_LOG, "a", encoding="utf-8") as f:
        if new:
            f.write("# Stage log\n\nAppended by `eval/run_benchmarks.py`. One block per run.\n")
        f.write(f"\n## Run {stamp}\n\n| Phase | Metric | v1 baseline | Current | Target | Pass |\n|---|---|---|---|---|---|\n")
        for g in gates:
            f.write(f"| P{g.phase} | {g.name} | {g.baseline} | {g.value} | {g.target} | {'✅' if g.passed else '❌'} |\n")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", type=int, default=max(PHASES))
    ap.add_argument("--llm", action="store_true", help="include live-LLM benchmarks (Phase 5+)")
    ap.add_argument("--no-log", action="store_true", help="do not append to STAGE_LOG.md")
    ap.add_argument("--only", type=str, default=None, help="comma-separated phases to run alone, e.g. 9 or 5,9")
    args = ap.parse_args()
    only = {int(x) for x in args.only.split(",")} if args.only else None

    global RUN_PHASE
    RUN_PHASE = args.phase
    gates: list[Gate] = []
    with TestClient(app) as client:
        for p in sorted(PHASES):
            if only is not None:
                if p in only:
                    gates.extend(PHASES[p](client, llm=args.llm) if p >= 5 else PHASES[p](client))
                continue
            if p == 8 and p <= args.phase:
                gates.extend(phase8(client, llm=args.llm, prior=list(gates)))
            elif p <= args.phase:
                gates.extend(PHASES[p](client, llm=args.llm) if p >= 5 else PHASES[p](client))

    w = max(len(g.name) for g in gates)
    print(f"{'ph':<3} {'metric':<{w}}  {'value':<40} {'target':<22} pass")
    for g in gates:
        print(f"P{g.phase:<2} {g.name:<{w}}  {str(g.value):<40} {g.target:<22} {'PASS' if g.passed else 'FAIL'}")

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps({"timestamp": stamp, "phase": args.phase, "gates": [g.row() for g in gates]},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    if not args.no_log:
        append_stage_log(gates, stamp)
        if args.phase == max(PHASES) and only is None:
            write_summary(gates, stamp, args.llm)
    failed = [g for g in gates if not g.passed]
    print(f"\n{len(gates) - len(failed)}/{len(gates)} gates passed -> {out.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
