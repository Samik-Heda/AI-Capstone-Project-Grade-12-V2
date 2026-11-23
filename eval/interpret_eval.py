"""Live evaluation of the §18.1 LLM fallback (needs Ollama). Usage: python eval/interpret_eval.py [--split dev|holdout]

Every row is a message the rules cannot read. For each: send it, call /api/interpret, and check that
the LLM's industry is one of the acceptable keys ("unknown" = no suggestion is the right outcome), that
nothing changed in the session before Apply, and how long it took."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SUITE = ROOT / "eval" / "interpret_suite.jsonl"


def load(split: str | None = None) -> list[dict]:
    rows = [json.loads(x) for x in open(SUITE, encoding="utf-8") if x.strip()]
    return [r for r in rows if split is None or r["split"] == split]


def run(client, rows: list[dict], think_s: float = 0.0) -> list[dict]:
    out = []
    for r in rows:
        sid = client.post("/api/session").json()["session_id"]
        if r.get("profile"):
            client.post(f"/api/session/{sid}/profile", json={"profile_id": r["profile"]})
        if r.get("question"):  # answer mode: the advisor's question is open
            from backend.session import STORE
            STORE.get(sid).pending_question = {**r["question"], "turn": 0}
        m = client.post("/api/message", json={"session_id": sid, "text": r["text"]}).json()
        rec = {"text": r["text"], "expect": r["expect"], "split": r["split"], "kind": m["kind"],
               "reached": m["interpret"]["available"]}
        if not rec["reached"]:
            out.append(rec)
            continue
        before = client.get(f"/api/session/{sid}").json()["state"]
        t0 = time.perf_counter()
        res = client.post("/api/interpret", json={"session_id": sid, "turn_id": m["turn_id"]}).json()
        rec["ms"] = round((time.perf_counter() - t0) * 1000)
        after = client.get(f"/api/session/{sid}").json()["state"]
        got = (res.get("result") or "").split(":", 1)[-1] or None
        rec.update(status=res["status"], result=got, info=res.get("info"),
                   silent_change=(before["profile"] != after["profile"] or before["weights"] != after["weights"]
                                  or before["modifiers"] != after["modifiers"]),
                   message_ok=(res["message"] is None) == (res["status"] != "suggested"))
        want = r["expect"]
        if r.get("question"):
            from backend.interpret import ANSWERS
            rec["correct"] = got in {ANSWERS[e] for e in want}
        else:
            rec["correct"] = (got in want) if got else ("unknown" in want and res["status"] == "none")
        out.append(rec)
        if think_s:
            time.sleep(think_s)
    return out


def summarize(res: list[dict]) -> dict:
    reached = [x for x in res if x.get("reached")]
    errors = [x for x in reached if x.get("status") == "error"]
    ms = [x["ms"] for x in reached if "ms" in x and x.get("status") != "error"]
    pct = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else None
    return {"rows": len(res), "reached": len(reached), "correct": sum(1 for x in reached if x.get("correct")),
            "accuracy": (sum(1 for x in reached if x.get("correct")) / len(reached)) if reached else 0.0,
            "silent_changes": [x["text"] for x in reached if x.get("silent_change")],
            "message_mismatch": [x["text"] for x in reached if not x.get("message_ok", True)],
            "errors": [(x["text"], (x.get("info") or {}).get("error")) for x in errors],
            "wrong": [(x["text"], x.get("result"), x["expect"]) for x in reached if not x.get("correct")],
            "p50_ms": pct(ms, 50), "p90_ms": pct(ms, 90), "mean_ms": round(statistics.mean(ms)) if ms else None}


def wait_quality(app, timeout: float = 300) -> bool:
    """Wait for the background load of the quality model (§18.1), if this machine uses it."""
    st = app.state.ollama
    t0 = time.perf_counter()
    while st.wants_quality and not st.quality_warm and time.perf_counter() - t0 < timeout:
        time.sleep(2)
    return st.quality_warm


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "holdout", "answer"], default=None)
    args = ap.parse_args()
    from fastapi.testclient import TestClient
    from backend.main import app
    from eval.narration_eval import wait_warm
    with TestClient(app) as client:
        wait_warm(app)
        from backend.embeddings import INDEX
        INDEX.wait_ready(180)
        wait_quality(app)
        res = run(client, load(args.split))
    for split in sorted({x["split"] for x in res}):
        sm = summarize([x for x in res if x["split"] == split])
        print(split, json.dumps(sm, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
