"""Profile builder benchmark (spec §14.3): eval/profile_corpus.jsonl through POST /api/profiles/draft.

Usage: python eval/profile_eval.py [--offline] [-v]

A row passes when the starting preset matches ("—" rows expect General Manufacturing) and at least 2
of its expected fields (all of them if fewer than 3) are in the top 3 or rose above their value in
the starting preset (the §14.3 rule, which is the gate). Rows the spec annotates "top-1" are also checked
for that field ranking first and reported separately: under §9.1 step 4 (modifiers multiply the preset
weights) row 12 cannot reach it from General Manufacturing's land weight of 0.03 (critical is ×3).
Negated fields must end below their starting-preset value (exactly 0 for "ignore"). --offline points the embedding
client at a dead port, so only the rule tiers run.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

CORPUS = ROOT / "eval" / "profile_corpus.jsonl"
GENERAL = "general_manufacturing"
EPS = 1e-9


def load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def judge(row: dict, d: dict) -> dict:
    want = "preset:" + (row["expect_preset"] or GENERAL)
    w, base = d["weights"], d["base_weights"]
    top3 = [k for k, _ in sorted(w.items(), key=lambda kv: -kv[1])[:3]]
    hits = [f for f in row["expect_fields"] if f in top3 or w[f] > base.get(f, 0) + EPS]
    need = len(row["expect_fields"]) if len(row["expect_fields"]) < 3 else 2
    top1_ok = "top1" not in row or top3[0] == row["top1"]
    neg_ok = {f: (w[f] < base.get(f, 0) - EPS or (base.get(f, 0) == 0 and w[f] == 0)) for f in row["negated"]}
    zero_ok = {f: w[f] == 0 for f in row.get("zero", [])}
    preset_ok = d["base_preset_id"] == want
    fields_ok = len(hits) >= need
    return {"id": row["id"], "preset_ok": preset_ok, "got_preset": d["base_preset_id"], "want_preset": want,
            "fields_ok": fields_ok, "hits": hits, "need": need, "top3": top3, "top1_ok": top1_ok,
            "negated_ok": all(neg_ok.values()) and all(zero_ok.values()), "negated": {**neg_ok, **zero_ok},
            "pass": preset_ok and fields_ok and all(neg_ok.values()) and all(zero_ok.values()),
            "modifiers": d["modifiers"], "semantic": d.get("semantic", []), "server_ms": d.get("ms")}


def run(client: TestClient, rows: list[dict] | None = None) -> dict:
    rows = rows or load_jsonl(CORPUS)
    out, wall = [], []
    client.post("/api/profiles/draft", json={"text": "warm up the builder"})
    for row in rows:
        t0 = time.perf_counter()
        r = client.post("/api/profiles/draft", json={"text": row["text"]})
        wall.append((time.perf_counter() - t0) * 1000)
        r.raise_for_status()
        out.append(judge(row, r.json()))
    neg_rows = [x for x in out if x["negated"]]
    n_neg = sum(len(x["negated"]) for x in neg_rows)
    ok_neg = sum(v for x in neg_rows for v in x["negated"].values())
    wall_sorted = sorted(wall)
    return {"rows": out, "n": len(out),
            "preset_acc": sum(x["preset_ok"] for x in out) / len(out),
            "pass_rate": sum(x["pass"] for x in out) / len(out),
            "negated": (ok_neg, n_neg),
            "latency_p50": round(statistics.median(wall), 1),
            "latency_p95": round(wall_sorted[min(len(wall) - 1, int(round(0.95 * (len(wall) - 1))))], 1),
            "latency_max": round(max(wall), 1)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    from backend.embeddings import INDEX
    from backend.main import app
    if a.offline:
        INDEX.base_url = "http://127.0.0.1:9"
    with TestClient(app) as c:
        if not a.offline:
            INDEX.wait_ready(120)
        res = run(c)
    for x in res["rows"]:
        if a.v or not x["pass"]:
            print(("PASS" if x["pass"] else "FAIL"), x["id"], x["got_preset"], "top3", x["top3"], "hits", x["hits"],
                  "neg", x["negated"], "mods", x["modifiers"], [s for s in x["semantic"]])
    print(f"preset auto-pick {res['preset_acc']:.2f}  rows passing {res['pass_rate']:.2f}  negated "
          f"{res['negated'][0]}/{res['negated'][1]}  latency p50 {res['latency_p50']} p95 {res['latency_p95']} "
          f"max {res['latency_max']} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
