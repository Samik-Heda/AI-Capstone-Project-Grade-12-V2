"""Phase 10 gates (spec §19): conversational answers, what-if, curated industry needs, stale-code banner.
Called from run_benchmarks.py (`--only 10`). Gate names start with the stage tag, e.g. "[P10a] …"."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from eval.p9_bench import _pytest, _session

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "eval" / "ask_suite.jsonl"
# a standalone figure in general advice; "tier-1" and "24-hour" (curated need names) are not figures
ADVICE_NUM = re.compile(r"(?<![\w-])\d[\d,.]*(?![\w-])")
STOP = {"access", "within", "nearby", "supply", "their", "should", "about", "which", "build"}


def suite() -> list[dict]:
    return [json.loads(x) for x in open(SUITE, encoding="utf-8")]


def p10a(client, llm: bool, Gate, pctl) -> list:
    from backend import router
    from backend.session import Session
    from eval import intent_eval as ie
    gates = []
    ok, tail = _pytest("tests/test_ask.py")
    gates.append(Gate(10, "[P10a] ask / what-if / needs / stale-flag unit tests", tail, "all green", ok))
    rows, bad, changed, times = suite(), [], [], []
    for r in rows:
        s = Session(id="p10")
        s.set_profile(r["profile"])
        w = dict(s.weights)
        t0 = time.perf_counter()
        x = router.handle(s, r["text"])
        times.append((time.perf_counter() - t0) * 1000)
        kind = "ask" if x["kind"] == "chat" and r["expect"] == "ask" else x["kind"]
        if kind != r["expect"]:
            bad.append((r["text"], r["expect"], x["kind"]))
        if r["expect"] != "adjust" and s.weights != w:
            changed.append(r["text"])
    acc = 1 - len(bad) / len(rows)
    gates.append(Gate(10, f"[P10a] question / what-if / command routing ({len(rows)} rows)", f"{acc:.3f}"
                      + (f" {bad[:3]}" if bad else ""), "≥ 0.95", acc >= 0.95, "blind first run 0.914"))
    gates.append(Gate(10, "[P10a] weight changes from questions or what-ifs before Apply", len(changed), "0", not changed))
    gates.append(Gate(10, f"[P10a] routing p95 (ms, n={len(times)})", round(pctl(times, 95), 2), "< 100",
                      pctl(times, 95) < 100))
    res = [x for x in ie.run_corpus(client, ie.load_jsonl(ie.CORPUS)) if "error" not in x]
    test = [x for x in res if x["row"]["split"] == "test"]
    acc = ie.accuracy(test)
    gates.append(Gate(10, f"[P10a] P4 intent accuracy unchanged (held-out {len(test)}; chat → ask accepted)",
                      f"{acc:.3f}", "≥ 0.92", acc >= 0.92))
    allr = list(res)
    for p in (ie.DEV, ie.HOLDOUT2, ie.HOLDOUT3, ie.HOLDOUT4):
        allr += [x for x in ie.run_corpus(client, ie.load_jsonl(p)) if "error" not in x]
    adv, silent, wrong = ie.adversarial_fp(res), ie.silent_switches(allr), ie.wrong_direction(allr)
    gates.append(Gate(10, "[P10a] adversarial FP / silent switches / wrong-direction modifiers (all P4 sets)",
                      f"{len(adv)} / {len(silent)} / {len(wrong)}", "0 / 0 / 0", not adv and not silent and not wrong))
    ok, tail = _pytest("tests/test_ask.py", "-k", "transcript")
    gates.append(Gate(10, "[P10a] the user's 2026-10-04 transcript replays (semiconductors, workers, questions)", tail,
                      "pass", ok))
    return gates


def _need_words(needs: list[dict]) -> list[set[str]]:
    return [{w for w in re.findall(r"[a-z][a-z-]+", n["need"].lower()) if len(w) >= 5 and w not in STOP} for n in needs]


def ask_live(client, rows: list[dict], quality: bool) -> list[dict]:
    from backend import db
    from backend.narrate import GENERAL_MARK
    from eval.narration_eval import parse_sse
    out = []
    for r in rows:
        sid = _session(client, r["profile"])
        m = client.post("/api/message", json={"session_id": sid, "text": r["text"]}).json()
        t0 = time.perf_counter()
        evs = parse_sse([client.post("/api/narrate", json={"session_id": sid, "turn_id": m["turn_id"],
                                                            "quality": quality}).text])
        text = "".join(d["t"] for e, d in evs if e == "token")
        ev, data = evs[-1]
        g = data.get("grounding") or {}
        cut = text.lower().find(GENERAL_MARK.lower())
        advice = text[cut:] if cut >= 0 else ""
        words = set(re.findall(r"[a-z][a-z-]+", text.lower()))
        needs = _need_words(db.industry_needs(r["profile"]))
        out.append({"text": r["text"], "event": ev, "s": round(time.perf_counter() - t0, 1),
                    "first_ms": data.get("first_token_ms"), "grounding": g, "answer": text,
                    "has_advice": cut >= 0, "advice_numbers": bool(ADVICE_NUM.search(advice)),
                    "mentions_need": any(n & words for n in needs), "error": data.get("message")})
    return out


OTHER_FACTORS = re.compile(r"other factors|might be required|anything else|what else|hidden costs|check")


def p10b(client, llm: bool, Gate, pctl) -> list:
    if not llm:
        return [Gate(10, "[P10b] live answers", "not run", "run with --llm", False)]
    from eval.interpret_eval import wait_quality
    from eval.narration_eval import wait_warm
    wait_warm(client.app)
    gates = []
    asks = [r for r in suite() if r["expect"] == "ask"][:20]
    res = ask_live(client, asks, quality=False)
    done = [x for x in res if x["event"] == "done"]
    nums = sum(x["grounding"].get("numbers_supported", 0) for x in done)
    tot = sum(x["grounding"].get("numbers_total", 0) for x in done)
    share = nums / tot if tot else 1.0
    unknown = [x["text"] for x in done if x["grounding"].get("unknown_districts")]
    first = [x["first_ms"] or 15000 for x in res]
    gates.append(Gate(10, f"[P10b] 3B answers finished ({len(asks)} questions)",
                      f"{len(done)}/{len(asks)}" + (f" errors {[x['error'] for x in res if x['event'] != 'done'][:2]}"
                                                     if len(done) < len(asks) else ""), "≥ 18", len(done) >= 18))
    gates.append(Gate(10, "[P10b] numbers supported / unknown districts in 3B answers", f"{share:.3f} of {tot} / {len(unknown)}"
                      + (f" {unknown[:2]}" if unknown else ""), "≥ 0.97 / 0", share >= 0.97 and not unknown))
    gates.append(Gate(10, "[P10b] 3B answer first token p50 (ms, timeouts counted at 15 s)", pctl(first, 50), "< 8000",
                      pctl(first, 50) < 8000))
    other = [x for x in done if OTHER_FACTORS.search(x["text"].lower())]
    gates.append(Gate(10, f"[P10b] 'what else / other factors' answers mention a curated need ({len(other)})",
                      f"{sum(x['mentions_need'] for x in other)}/{len(other)}", "100%",
                      bool(other) and all(x["mentions_need"] for x in other)))
    adv = [x for x in done if x["has_advice"]]
    clean = sum(not x["advice_numbers"] for x in adv)
    gates.append(Gate(10, f"[P10b] general advice without numbers ({len(adv)} answers with advice)",
                      f"{clean}/{len(adv)}", "≥ 95%", not adv or clean / len(adv) >= 0.95))
    wait_quality(client.app)
    more = ask_live(client, [r for r in asks if OTHER_FACTORS.search(r["text"].lower())][:3] + asks[:2], quality=True)
    mdone = [x for x in more if x["event"] == "done"]
    mn = sum(x["grounding"].get("numbers_supported", 0) for x in mdone)
    mt = sum(x["grounding"].get("numbers_total", 0) for x in mdone)
    slow = max((x["s"] for x in more), default=0)
    gates.append(Gate(10, f"[P10b] Tell me more (7B): finished, slowest, numbers supported ({len(more)} questions)",
                      f"{len(mdone)}/{len(more)}, {slow} s, {mn}/{mt}", "all, < 150 s, ≥ 0.97",
                      len(mdone) == len(more) and slow < 150 and (mt == 0 or mn / mt >= 0.97)))
    RES = ROOT / "eval" / "results"
    RES.mkdir(exist_ok=True)
    (RES / f"ask-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(res + more, indent=1, ensure_ascii=False),
                                                                    encoding="utf-8")
    return gates


def p10c(client, llm: bool, Gate, pctl) -> list:
    from backend import advisor, brief, db, router
    from backend.session import Session
    from eval import p9_bench
    gates = []
    wi = [r for r in suite() if r["expect"] == "what_if"]
    bad = []
    for r in wi:
        s = Session(id="w")
        s.set_profile(r["profile"])
        x = router.handle(s, r["text"])
        if x["kind"] != "what_if" or x["facts"]["preview"] != advisor.preview(s, x["facts"]["changes"]):
            bad.append(r["text"])
    gates.append(Gate(10, f"[P10c] what-if previews equal a non-committing re-rank ({len(wi)})",
                      f"{len(wi) - len(bad)}/{len(wi)}" + (f" {bad[:2]}" if bad else ""), "100%", not bad))
    presets = [p["id"] for p in db.profiles() if p["kind"] == "preset"]
    cov = [p for p in presets if 4 <= len(db.industry_needs(p)) <= 6]
    gates.append(Gate(10, "[P10c] curated industry needs cover every preset (4–6 each)", f"{len(cov)}/12", "12/12",
                      len(cov) == 12))
    miss = [p for p in presets if not brief.build(_preset_session(p))["also_check"]]
    gates.append(Gate(10, "[P10c] brief has 'Also check before investing' for every preset", f"{12 - len(miss)}/12",
                      "12/12", not miss))
    gates += [type(g)(10, g.name.replace("[P9d]", "[P10c] P9d:"), g.value, g.target, g.passed)
              for g in p9_bench.p9d(client, llm, Gate, pctl)]
    return gates


def _preset_session(pid):
    from backend.session import Session
    s = Session(id="b")
    s.set_profile(pid)
    return s


def p10d(client, llm: bool, Gate, pctl) -> list:
    from eval import ui_runner
    if not llm:
        return [Gate(10, "[P10d] §19 UI walkthrough (steps 26–29)", "not run", "run with --llm", False)]
    res = ui_runner.run(to=29, frm=26, build=True, timing=False)
    if "error" in res:
        return [Gate(10, "[P10d] §19 UI walkthrough (steps 26–29)", res["error"][:200], "pass", False)]
    gates = []
    for r in res["runs"]:
        failed = [(st["step"], st["detail"][:80]) for st in r.get("steps", []) if not st["pass"]]
        con = r.get("console", []) + r.get("errors", [])
        gates.append(Gate(10, f"[P10d] §19 UI walkthrough steps 26–29 at {r.get('viewport')}, 0 console errors",
                          f"{r.get('passed')}/{r.get('total')}" + (f" {failed}" if failed else "")
                          + (f" console {con[:2]}" if con else ""), "4/4, 0 errors",
                          r.get("passed") == r.get("total") == 4 and not con))
    return gates


STAGES = [p10a, p10b, p10c, p10d]


def phase10(client, llm: bool, Gate, pctl) -> list:
    gates = []
    for stage in STAGES:
        t0 = time.perf_counter()
        gates.extend(stage(client, llm, Gate, pctl))
        print(f"  {stage.__name__} done in {time.perf_counter() - t0:.0f} s", flush=True)
    return gates
