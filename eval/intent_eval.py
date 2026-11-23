"""Phase 4 evaluation helpers: intent corpus, conversation scripts and τ/δ calibration (spec §14).

Used by run_benchmarks.py and by `python eval/intent_eval.py --calibrate`, which grid-searches τ/δ
on the 30% calibration split only and writes the result to config.toml [intent]."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import intent  # noqa: E402

CORPUS = ROOT / "eval" / "intent_corpus.jsonl"
CONVERSATIONS = ROOT / "eval" / "conversations.jsonl"
DEV = ROOT / "eval" / "intent_fresh.jsonl"          # tuning set (used for τ/δ calibration)
HOLDOUT2 = ROOT / "eval" / "intent_holdout2.jsonl"
HOLDOUT3 = ROOT / "eval" / "intent_holdout3.jsonl"
HOLDOUT4 = ROOT / "eval" / "intent_holdout4.jsonl"
_SIGN = {"exclude": -1, "low": -1, "high": 1, "critical": 1}
# Kinds that may change the session's industry: an explicit chip/menu choice or a save (§7.2 rule 3/3b).
EXPLICIT_SWITCH = {"apply_preset", "clarify_answer", "save_profile"}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def new_session(client, profile: str | None = None) -> str:
    sid = client.post("/api/session").json()["session_id"]
    if profile:
        r = client.post(f"/api/session/{sid}/profile", json={"profile_id": profile})
        assert r.status_code == 200, r.text
    return sid


def run_row(client, row: dict) -> dict:
    sid = new_session(client, row.get("ctx_profile"))
    t = time.perf_counter()
    resp = client.post("/api/message", json={"session_id": sid, "text": row["text"]})
    wall = (time.perf_counter() - t) * 1000
    if resp.status_code != 200:
        return {"row": row, "error": f"HTTP {resp.status_code}: {resp.text[:200]}", "ok": False}
    body = resp.json()
    st = body["state"]
    prof = st["profile"]["id"] if st["profile"] else None
    silent = prof != row.get("ctx_profile") and body["kind"] not in EXPLICIT_SWITCH and row.get("ctx_profile") is not None
    if prof and prof.startswith("user:") and prof != row.get("ctx_profile"):
        client.delete(f"/api/profiles/{prof}")  # saved by a save row; keep the DB clean
    # A suggestion is judged on what it offers (the pending preset); nothing may have switched.
    offered = (st.get("pending_suggest") or {}).get("preset_id") if body["kind"] == "suggest_preset" else prof
    und = body["understood"]
    mods = {m["field"]: m["label"] for m in und["modifiers"]}
    # §19.1: a question that used to fall through to "chat" is now answered as "ask" (same reply, plus the AI answer)
    kind_ok = body["kind"] == row["expect_kind"] or (row["expect_kind"] == "chat" and body["kind"] == "ask")
    want = row["expect_profile"]
    prof_ok = (offered or "").startswith("user:") if want == "$user" else offered == want
    if body["kind"] == "suggest_preset" and prof != row.get("ctx_profile"):
        prof_ok = False
    dist_ok = row.get("expect_district") is None or (und["districts"][:1] == [row["expect_district"]])
    mods_ok = row["expect_modifiers"] is None or mods == row["expect_modifiers"]
    unparsed_ok = "expect_unparsed" not in row or und["unparsed_fields"] == row["expect_unparsed"]
    ind = und["industry"] or {}
    return {"row": row, "kind": body["kind"], "profile": offered, "session_profile": prof, "silent_switch": silent,
            "mods": mods, "unparsed": und["unparsed_fields"],
            "districts": und["districts"], "tier": ind.get("tier"), "status": ind.get("status"),
            "intent_ok": kind_ok and prof_ok and dist_ok, "mods_ok": mods_ok, "unparsed_ok": unparsed_ok,
            "route_ms": body["timing_ms"]["route"], "server_ms": body["timing_ms"]["total"], "wall_ms": wall,
            "reply": body["reply_md"]}


def run_corpus(client, rows: list[dict]) -> list[dict]:
    return [run_row(client, r) for r in rows]


def accuracy(results: list[dict]) -> float:
    return sum(r["intent_ok"] for r in results) / max(1, len(results))


def modifier_f1(results: list[dict]) -> dict:
    tp = fp = fn = 0
    for r in results:
        exp = r["row"]["expect_modifiers"]
        if exp is None or "error" in r:
            continue
        got = set(r["mods"].items())
        want = set(exp.items())
        tp += len(got & want)
        fp += len(got - want)
        fn += len(want - got)
    p = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * p * rec / (p + rec) if p + rec else 0.0
    return {"f1": f1, "precision": p, "recall": rec, "tp": tp, "fp": fp, "fn": fn}


def negation_pass(results: list[dict]) -> tuple[int, int]:
    rows = [r for r in results if "negation" in r["row"]["tags"]]
    ok = 0
    for r in rows:
        want = r["row"]["expect_modifiers"]
        neg = {f for f, lab in want.items() if lab in ("low", "exclude")}
        ok += (set(r["mods"]) == set(want) and all(r["mods"][f] in ("low", "exclude") for f in neg))
    return ok, len(rows)


def adversarial_fp(results: list[dict]) -> list[str]:
    """Rows where a substring produced a wrong industry or a wrong field."""
    bad = []
    for r in results:
        if "adversarial" not in r["row"]["tags"]:
            continue
        exp = r["row"]["expect_modifiers"] or {}
        wrong_field = any(f not in exp for f in r["mods"]) or any(f not in exp and f not in r["row"].get("expect_unparsed", [])
                                                                   for f in r["unparsed"])
        if r["profile"] != r["row"]["expect_profile"] or wrong_field:
            bad.append(r["row"]["text"])
    return bad


def wrong_direction(results: list[dict]) -> list[str]:
    """Rows where a field moved the opposite way to what was asked (worse than a miss)."""
    bad = []
    for r in results:
        exp = r["row"].get("expect_modifiers") or {}
        if any(f in exp and _SIGN[lab] != _SIGN[exp[f]] for f, lab in r.get("mods", {}).items()):
            bad.append(r["row"]["text"])
    return bad


def silent_switches(results: list[dict]) -> list[str]:
    """Rows where the session's industry changed without an Apply/clarify chip or a save (§7.2)."""
    return [r["row"]["text"] for r in results if r.get("silent_switch")]


def clarify_recall(results: list[dict]) -> tuple[int, int]:
    rows = [r for r in results if r["row"]["expect_kind"] == "clarify"]
    return sum(r["kind"] == "clarify" for r in rows), len(rows)


# ---------- conversations (§14.5) ----------

def run_conversation(client, conv: dict) -> tuple[bool, list[str]]:
    """Each step: {say | chip | select_profile | sliders | build_profile | save_profile, expect:{...}}. `chip` clicks a chip
    of the previous reply by its label (the UI sends the chip's value as the message)."""
    sid = new_session(client, conv.get("ctx_profile"))
    saved: dict = {}
    created: list[str] = []
    errors: list[str] = []
    prev_ranking = None
    last_chips: list[dict] = []
    for i, step in enumerate(conv["steps"]):
        body = None
        if "chip" in step:
            # Click a chip from the previous reply, by its label; the UI sends its value as the message.
            chip = next((c for c in last_chips if c["label"] == step["chip"]), None)
            if chip is None:
                errors.append(f"step {i}: no chip {step['chip']!r} in {[c['label'] for c in last_chips]}")
                continue
            r = client.post("/api/message", json={"session_id": sid, "text": chip["value"]})
        elif "say" in step:
            r = client.post("/api/message", json={"session_id": sid, "text": step["say"]})
        elif "select_profile" in step:
            r = client.post(f"/api/session/{sid}/profile", json={"profile_id": step["select_profile"]})
        elif "sliders" in step:
            r = client.post(f"/api/session/{sid}/weights", json={"overrides": step["sliders"]})
        elif "build_profile" in step:
            # The builder (§9): draft from a sentence, Save (POST /profiles with the draft's weights), Use now.
            b = step["build_profile"]
            d = client.post("/api/profiles/draft", json={"text": b["text"]})
            if d.status_code != 200:
                errors.append(f"step {i}: draft HTTP {d.status_code} {d.text[:120]}")
                continue
            d = d.json()
            if b.get("expect_base") and d["base_preset_id"] != b["expect_base"]:
                errors.append(f"step {i}: draft started from {d['base_preset_id']}, not {b['expect_base']}")
            r = client.post("/api/profiles", json={"name": b["name"], "weights": d["weights"], "constraints": d["constraints"],
                                                   "base_profile_id": d["base_preset_id"], "description": b["text"],
                                                   "provenance": {"source": "builder", "modifiers": d["modifiers"]}})
            saved = r.json().get("profile", {})
            created.append(saved["id"])
            r = client.post(f"/api/session/{sid}/profile", json={"profile_id": saved["id"]})
        elif "save_profile" in step:
            r = client.post("/api/profiles", json=step["save_profile"])
            saved = r.json().get("profile", {})
            created.append(saved["id"])
            r = client.post(f"/api/session/{sid}/profile", json={"profile_id": saved["id"]})
        else:
            errors.append(f"step {i}: unknown action")
            continue
        if r.status_code != 200:
            errors.append(f"step {i}: HTTP {r.status_code} {r.text[:120]}")
            continue
        body = r.json()
        st = body["state"]
        exp = step.get("expect", {})
        prof = st["profile"]["id"] if st["profile"] else None
        last_chips = body.get("chips", [])
        if body.get("kind") == "save_profile" and prof and prof.startswith("user:") and prof not in created:
            created.append(prof)
            saved = {"id": prof, "weights": dict(st["weights"])}
        if "pending" in exp and (st.get("pending_suggest") or {}).get("preset_id") != exp["pending"]:
            errors.append(f"step {i}: pending {st.get('pending_suggest')} != {exp['pending']}")
        if "pending_modifiers" in exp and (st.get("pending_suggest") or {}).get("modifiers") != exp["pending_modifiers"]:
            errors.append(f"step {i}: pending modifiers {(st.get('pending_suggest') or {}).get('modifiers')} != {exp['pending_modifiers']}")
        if "chips_count" in exp and len(last_chips) != exp["chips_count"]:
            errors.append(f"step {i}: {len(last_chips)} chips != {exp['chips_count']}")
        if "ranked" in exp and bool(st["ranking"]) != exp["ranked"]:
            errors.append(f"step {i}: ranked {bool(st['ranking'])} != {exp['ranked']}")
        if "kind" in exp and body.get("kind") != exp["kind"]:
            errors.append(f"step {i}: kind {body.get('kind')} != {exp['kind']}")
        if "profile" in exp:
            want = saved.get("id") if exp["profile"] == "$saved" else exp["profile"]
            if exp["profile"] == "$user":
                want = prof if prof and prof.startswith("user:") else "user:*"
            if prof != want:
                errors.append(f"step {i}: profile {prof} != {want}")
        if "modifiers" in exp and st["modifiers"] != exp["modifiers"]:
            errors.append(f"step {i}: modifiers {st['modifiers']} != {exp['modifiers']}")
        if "modifiers_include" in exp:
            for f, lab in exp["modifiers_include"].items():
                if st["modifiers"].get(f) != lab:
                    errors.append(f"step {i}: modifier {f}={st['modifiers'].get(f)} != {lab}")
        if "overrides" in exp and st["slider_overrides"] != exp["overrides"]:
            errors.append(f"step {i}: overrides {st['slider_overrides']} != {exp['overrides']}")
        if exp.get("weights_equal_base") and any(abs(st["weights"][k] - st["base_weights"][k]) > 1e-9 for k in st["weights"]):
            errors.append(f"step {i}: weights differ from base")
        if exp.get("weights_equal_saved"):
            sw = saved["weights"]
            if any(abs(st["weights"][k] - sw.get(k, 0.0)) > 1e-9 for k in st["weights"]):
                errors.append(f"step {i}: weights differ from the saved profile")
        if "focus" in exp and st["focus_district"] != exp["focus"]:
            errors.append(f"step {i}: focus {st['focus_district']} != {exp['focus']}")
        if exp.get("ranking_unchanged") and prev_ranking is not None:
            if [(e["district_id"], e["score"]) for e in st["ranking"]] != prev_ranking:
                errors.append(f"step {i}: ranking changed")
        if "weight_share" in exp:
            for f, w in exp["weight_share"].items():
                if abs(st["weights"][f] - w) > 1e-9:
                    errors.append(f"step {i}: weight {f}={st['weights'][f]:.4f} != {w}")
        if "weight_above_base" in exp:
            for f in exp["weight_above_base"]:
                if not st["weights"][f] > st["base_weights"][f]:
                    errors.append(f"step {i}: {f} not above base")
        if exp.get("chip_contains"):
            labels = [c["label"] for c in body.get("chips", [])]
            if exp["chip_contains"] not in labels:
                errors.append(f"step {i}: chips {labels} lack {exp['chip_contains']}")
        if exp.get("no_industry"):
            if prof and prof != exp.get("profile", prof):
                errors.append(f"step {i}: industry changed to {prof}")
        prev_ranking = [(e["district_id"], e["score"]) for e in st["ranking"]]
    for p in created:
        client.delete(f"/api/profiles/{p}")
    return not errors, errors



# ---------- τ/δ calibration ----------

def calibrate(client, rows: list[dict]) -> dict:
    calib = [r for r in rows if r["split"] in ("calib", "dev")]
    taus = [round(0.40 + 0.01 * i, 2) for i in range(41)]
    deltas = [round(0.0 + 0.02 * i, 2) for i in range(31)]
    # Only rows that reach the embedding tier depend on τ/δ; score the rest once. Run with the gate
    # wide open (τ=δ=0) so every row that can reach the tier reports tier == "embedding".
    intent.set_thresholds(0.0, 0.0)
    probe = run_corpus(client, calib)
    varying = [p["row"] for p in probe if p["tier"] == "embedding"]
    fixed_ok = sum(p["intent_ok"] for p in probe if p["tier"] != "embedding")
    grid = []
    for tau in taus:
        for delta in deltas:
            intent.set_thresholds(tau, delta)
            ok = fixed_ok + sum(r["intent_ok"] for r in run_corpus(client, varying))
            grid.append((ok / len(calib), -abs(tau - 0.62) - abs(delta - 0.05), tau, delta))
    intent.set_thresholds(None)
    best = max(grid)
    return {"tau": best[2], "delta": best[3], "calib_accuracy": best[0], "n_calib": len(calib),
            "grid_size": len(grid)}


def write_thresholds(tau: float, delta: float, note: str) -> None:
    path = ROOT / "config.toml"
    text = path.read_text(encoding="utf-8")
    block = f"[intent]\n# {note}\ntau = {tau}\ndelta = {delta}\n"
    if "[intent]" in text:
        text = re.sub(r"\[intent\]\n(?:#.*\n)*tau = .*\ndelta = .*\n", block, text)
    else:
        text = text.rstrip("\n") + "\n\n" + block
    path.write_text(text, encoding="utf-8")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true", help="grid-search τ/δ on the calibration split, write config.toml")
    ap.add_argument("--show", choices=["fail", "all"], default="fail")
    ap.add_argument("--offline", action="store_true", help="disable the embedding tier")
    ap.add_argument("--corpus", default=str(CORPUS), help="JSONL corpus to evaluate")
    args = ap.parse_args()
    from fastapi.testclient import TestClient
    from backend.embeddings import INDEX
    from backend.main import app
    rows = load_jsonl(Path(args.corpus))
    for r in rows:
        r.setdefault("split", "test")
    with TestClient(app) as client:
        print("embeddings ready:", INDEX.wait_ready(180))
        if args.offline:
            INDEX.enabled = False
        if args.calibrate:
            dev = load_jsonl(DEV)
            for r in dev:
                r["split"] = "dev"
            res = calibrate(client, rows + dev)
            print(res)
            write_thresholds(res["tau"], res["delta"],
                             f"Calibrated by eval/intent_eval.py --calibrate on {res['n_calib']} rows (corpus calib split + intent_fresh dev set) "
                             f"(accuracy {res['calib_accuracy']:.3f}); spec start values τ=0.62, δ=0.05.")
            from backend import config
            config.load.cache_clear()
        results = run_corpus(client, rows)
        for split in sorted({r["row"]["split"] for r in results}):
            rs = [r for r in results if r["row"]["split"] == split]
            print(f"{split}: accuracy {accuracy(rs):.3f} ({sum(r['intent_ok'] for r in rs)}/{len(rs)})")
        print("modifier F1", modifier_f1(results), "negation", negation_pass(results),
              "adversarial FP", adversarial_fp(results), "clarify", clarify_recall(results))
        for r in results:
            if args.show == "all" or not (r["intent_ok"] and r["mods_ok"] and r["unparsed_ok"]):
                flag = "ok " if r["intent_ok"] and r["mods_ok"] else "BAD"
                print(f"{flag} [{r['row']['split']}] {r['row']['text']!r}: kind={r['kind']} prof={r['profile']} "
                      f"mods={r['mods']} unparsed={r['unparsed']} tier={r['tier']}/{r['status']} | expect "
                      f"{r['row']['expect_kind']} {r['row']['expect_profile']} {r['row']['expect_modifiers']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
