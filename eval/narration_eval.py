"""Narration benchmark (spec §8, §14.4): the 30-prompt chat suite against live Ollama.

Usage: python eval/narration_eval.py [--offline] [--only explain,compare]

Each row gets a fresh session on its profile, sends the prompt to /api/message, then streams
/api/narrate and checks the result with the groundedness checker. District placeholders
({r1}…{r5}, {last}) are resolved from the profile's current ranking, so the suite survives preset
re-tuning. --offline points narration at a dead port and checks that every turn still gets its
templated reply and /narrate degrades to an `error` event (no server errors).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend import scoring, templates  # noqa: E402

SUITE = ROOT / "eval" / "chat_suite.jsonl"
RESULTS = ROOT / "eval" / "results"
EXPECT_KIND = {"explain": {"explain_district"}, "compare": {"compare"},
               "why_not_first": {"compare", "explain_district"}, "refusal": {"refusal"}}


def load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def placeholders(profile_id: str) -> dict:
    p = scoring.load_profile(profile_id)
    res = scoring.rank(p["weights"], p["constraints"])
    elig = sorted((e for e in res["ranking"] if e["eligible"]), key=lambda e: e["rank"])
    out = {f"r{i + 1}": e["name"] for i, e in enumerate(elig[:5])}
    out["last"] = elig[-1]["name"]
    return out


def parse_sse(chunks) -> list[tuple[str, dict]]:
    """SSE parser that buffers partial lines across chunks (the same contract the frontend follows)."""
    events, buf = [], ""
    for chunk in chunks:
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            ev, data = None, []
            for line in block.splitlines():
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
            if ev:
                events.append((ev, json.loads("\n".join(data)) if data else {}))
    return events


def narrate(client: TestClient, sid: str, turn_id: str) -> dict:
    t0 = time.perf_counter()
    with client.stream("POST", "/api/narrate", json={"session_id": sid, "turn_id": turn_id}) as r:
        status = r.status_code
        events = parse_sse(r.iter_text())
    wall = (time.perf_counter() - t0) * 1000
    text = "".join(d.get("t", "") for e, d in events if e == "token")
    last = events[-1] if events else ("error", {"message": "no events"})
    return {"status": status, "text": text, "event": last[0], "data": last[1], "wall_ms": round(wall)}


def run_row(client: TestClient, row: dict) -> dict:
    sid = client.post("/api/session").json()["session_id"]
    client.post(f"/api/session/{sid}/profile", json={"profile_id": row["profile"]})
    text = row["text"].format(**placeholders(row["profile"]))
    r = client.post("/api/message", json={"session_id": sid, "text": text})
    out = {"id": row["id"], "task": row["task"], "text": text, "status": r.status_code}
    if r.status_code != 200:
        return out
    m = r.json()
    out.update(kind=m["kind"], reply_md=m["reply_md"], kind_ok=m["kind"] in EXPECT_KIND[row["task"]],
               narration_available=m["narration"]["available"])
    if m["narration"]["available"]:
        n = narrate(client, sid, m["turn_id"])
        out["narration"] = n
    return out


def refusal_ok(x: dict) -> bool:
    if x.get("kind") == "refusal" and x.get("reply_md", "").startswith(templates.REFUSAL):
        return True
    n = x.get("narration") or {}
    return n.get("event") == "done" and n["data"]["grounding"].get("refusal", False)


def summarize(res: list[dict]) -> dict:
    narrated = [x for x in res if x["task"] != "refusal" and (x.get("narration") or {}).get("event") == "done"]
    in_scope = [x for x in res if x["task"] != "refusal"]
    g = [x["narration"]["data"]["grounding"] for x in narrated]
    nums = sum(z["numbers_total"] for z in g)
    sup = sum(z["numbers_supported"] for z in g)
    compare_wrong = [x["id"] for x in narrated if x["task"] == "compare"
                     and x["narration"]["data"]["grounding"]["wrong_directions"]]
    # Timeouts count as the timeout itself, so the p50 is not computed over successes only.
    timeout_ms = 1000 * __import__("backend.config", fromlist=["load"]).load()["ollama"]["first_token_timeout_s"]
    attempted = [x for x in in_scope if x.get("narration")]
    ttft = [x["narration"]["data"].get("first_token_ms") or timeout_ms if x["narration"]["event"] == "done"
            else timeout_ms for x in attempted]
    total = [x["narration"]["data"]["ms"] for x in narrated]
    errors = [x["id"] for x in in_scope if (x.get("narration") or {}).get("event") == "error"]
    refusals = [x for x in res if x["task"] == "refusal"]
    return {
        "rows": len(res), "narrated": len(narrated), "in_scope": len(in_scope), "narration_errors": errors,
        "timeouts": [x["id"] for x in attempted if "timeout" in x["narration"]["data"].get("message", "")],
        "grounded_share": (sum(z["grounded"] for z in g) / len(g)) if g else 0.0,
        "ungrounded": [x["id"] for x in narrated if not x["narration"]["data"]["grounding"]["grounded"]],
        "numeric_supported_share": (sup / nums) if nums else 1.0, "numbers": nums,
        "compare_wrong_directions": compare_wrong,
        "all_wrong_directions": [x["id"] for x in narrated if x["narration"]["data"]["grounding"]["wrong_directions"]],
        "refusal": (sum(refusal_ok(x) for x in refusals), len(refusals)),
        "kind_errors": [x["id"] for x in res if not x.get("kind_ok", False)],
        "server_errors": [x["id"] for x in res if x.get("status") != 200
                          or (x.get("narration") or {}).get("status", 200) != 200],
        "ttft_p50": statistics.median(ttft) if ttft else None,
        "ttft_p90": sorted(ttft)[int(round(0.9 * (len(ttft) - 1)))] if ttft else None,
        "total_p90": sorted(total)[int(round(0.9 * (len(total) - 1)))] if total else None,
    }


def wait_warm(app, timeout: float = 240) -> bool:
    st = app.state.ollama
    t = time.time()
    while time.time() - t < timeout:
        if st.warm and not app.state.narrator.warming:
            return True
        time.sleep(0.5)
    return False


# A person reads each answer before asking the next question. Back-to-back requests on the reference
# laptop drain its turbo budget and slow prompt evaluation by ~50% (STAGE_LOG P5 notes), so latency is
# measured with this pause between prompts; --think 0 gives the back-to-back worst case.
THINK_S = 10.0


def run_live(client: TestClient, rows: list[dict], think_s: float = THINK_S) -> list[dict]:
    out = []
    for i, r in enumerate(rows):
        if i and think_s and r["task"] != "refusal":
            time.sleep(think_s)
        out.append(run_row(client, r))
    return out


def run_offline(client: TestClient, app, rows: list[dict]) -> dict:
    """Ollama 'stopped': dead URL and unreachable status. Every turn must still return its template."""
    from eval import intent_eval as ie
    st, nr = app.state.ollama, app.state.narrator
    saved = (st.reachable, st.models, st.warm, nr.base_url, nr.client)
    st.reachable, st.models, st.warm = False, set(), False
    try:
        res = []
        for row in rows:
            x = run_row(client, row)
            # Even when /message says unavailable, a client may still call /narrate: it must not 5xx.
            sid = client.post("/api/session").json()["session_id"]
            client.post(f"/api/session/{sid}/profile", json={"profile_id": row["profile"]})
            m = client.post("/api/message", json={"session_id": sid, "text": x["text"]}).json()
            x["forced_narrate"] = narrate(client, sid, m["turn_id"])
            res.append(x)
        # Same with Ollama "reachable" but the port dead (a crash between health checks).
        st.reachable, st.models = True, saved[1]
        nr.base_url, nr.client = "http://127.0.0.1:9", None
        dead = []
        for row in rows[:6]:
            sid = client.post("/api/session").json()["session_id"]
            client.post(f"/api/session/{sid}/profile", json={"profile_id": row["profile"]})
            m = client.post("/api/message", json={"session_id": sid,
                                                  "text": row["text"].format(**placeholders(row["profile"]))}).json()
            dead.append(narrate(client, sid, m["turn_id"]))
        st.reachable, st.models = False, set()
        convs = ie.load_jsonl(ie.CONVERSATIONS)
        conv = [ie.run_conversation(client, cv) for cv in convs]
    finally:
        st.reachable, st.models, st.warm = saved[0], saved[1], saved[2]
        nr.base_url, nr.client = saved[3], saved[4]
    turns = res
    templated = [x for x in turns if x.get("status") == 200 and x.get("reply_md")]
    server_err = [x["id"] for x in turns if x.get("status") != 200 or x["forced_narrate"]["status"] != 200]
    server_err += [f"dead-port-{i}" for i, d in enumerate(dead) if d["status"] != 200 or d["event"] != "error"]
    return {"turns": len(turns), "templated": len(templated), "server_errors": server_err,
            "forced_narrate_events": sorted({x["forced_narrate"]["event"] for x in turns}),
            "dead_port_events": sorted({d["event"] for d in dead}),
            "conversations": (sum(p for p, _ in conv), len(conv)),
            "narration_flag_off": all(not x.get("narration_available") for x in turns)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--think", type=float, default=THINK_S, help="seconds between prompts (user reading time)")
    args = ap.parse_args()
    from backend.main import app

    rows = load_jsonl(SUITE)
    if args.only:
        rows = [r for r in rows if r["task"] in args.only.split(",")]
    with TestClient(app) as client:
        if args.offline:
            print(json.dumps(run_offline(client, app, rows), indent=2))
            return 0
        print("warm:", wait_warm(app))
        res = run_live(client, rows, args.think)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"narration-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for x in res:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    for x in res:
        n = x.get("narration") or {}
        d = n.get("data", {})
        g = d.get("grounding", {})
        flag = "✓" if g.get("grounded") else ("—" if not n else "⚠")
        print(f"{x['id']:<12} {x.get('kind', '?'):<17} {flag} ttft={d.get('first_token_ms')} ms={d.get('ms')} "
              f"{g.get('unsupported_numbers') or ''} {g.get('unknown_districts') or ''} {g.get('wrong_directions') or ''}")
        if n.get("text"):
            print("      ", n["text"].replace("\n", " "))
        elif n:
            print("      ", d)
    print(json.dumps(summarize(res), indent=2))
    print("->", out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
