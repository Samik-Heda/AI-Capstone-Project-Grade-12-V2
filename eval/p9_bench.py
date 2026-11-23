"""Phase 9 gates (spec §18): stages P9a–P9e of the conversational advisor. Called from run_benchmarks.py
(`--only 9` runs just these). Gate names start with the stage tag, e.g. "[P9a] …"."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pytest(*files: str) -> tuple[bool, str]:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout.strip().splitlines() or ["?"])[-1]


def _session(client, profile: str | None = None) -> str:
    sid = client.post("/api/session").json()["session_id"]
    if profile:
        client.post(f"/api/session/{sid}/profile", json={"profile_id": profile})
    return sid


def route_p95(client, pctl, extra: list[tuple[str | None, list[str]]] | None = None) -> tuple[float, int]:
    """Server time of /api/message over the P4 intent corpus (fresh session per row, every third row on a
    profile so adjust/advisor paths run too) plus optional (profile, [turns]) scripts."""
    from eval import intent_eval as ie
    times = []
    for i, row in enumerate(ie.load_jsonl(ie.CORPUS)):
        sid = _session(client, "preset:automobile" if i % 3 == 0 else None)
        times.append(client.post("/api/message", json={"session_id": sid, "text": row["text"]}).json()["timing_ms"]["total"])
    for prof, turns in extra or []:
        sid = _session(client, prof)
        for t in turns:
            times.append(client.post("/api/message", json={"session_id": sid, "text": t}).json()["timing_ms"]["total"])
    return round(pctl(times, 95), 2), len(times)


def p9a(client, llm: bool, Gate, pctl) -> list:
    gates = []
    ok, tail = _pytest("tests/test_lane.py")
    gates.append(Gate(9, "[P9a] LLM lane unit tests (serialised, busy, abandoned, JSON)", tail, "all green", ok))
    ok, tail = _pytest()
    gates.append(Gate(9, "[P9a] full pytest suite", tail, "all green", ok))
    p95, n = route_p95(client, pctl)
    gates.append(Gate(9, f"[P9a] /api/message server time p95 (ms, n={n})", p95, "< 100", p95 < 100))
    if llm:
        from eval import narration_eval as ne
        ne.wait_warm(client.app)
        nr = client.app.state.narrator
        saved = nr.first_token_timeout, nr.total_timeout
        nr.first_token_timeout, nr.total_timeout = 30, 60  # measure queuing, not this laptop's prompt speed
        try:
            res = concurrent_notes(client)
        finally:
            nr.first_token_timeout, nr.total_timeout = saved
        gates.append(Gate(9, "[P9a] two notes at once, live (30 s limits): both finish, second waits its turn",
                          f"{res['events']} wait_ms {res['wait_ms']}", "done, done; 2nd waited",
                          res["events"] == ["done", "done"] and max(res["wait_ms"]) > 0))
    return gates


def concurrent_notes(client) -> dict:
    """Start two AI notes at the same moment (explain + compare) and check both finish."""
    from eval.narration_eval import parse_sse
    sid = _session(client, "preset:automobile")
    t1 = client.post("/api/message", json={"session_id": sid, "text": "tell me about Satara"}).json()["turn_id"]
    t2 = client.post("/api/message", json={"session_id": sid, "text": "compare Pune and Nashik"}).json()["turn_id"]
    out: dict[str, tuple] = {}

    def run(tid):
        evs = parse_sse([client.post("/api/narrate", json={"session_id": sid, "turn_id": tid}).text])
        out[tid] = evs[-1]

    ths = [threading.Thread(target=run, args=(t,)) for t in (t1, t2)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    last = [out[t1], out[t2]]
    return {"events": [e for e, _ in last], "wait_ms": [d.get("wait_ms", 0) for _, d in last]}


# ---------- P9b advisor ----------

def _expected_tip(s) -> str | None:
    """The §18.2 tip rule, restated independently of advisor.tip."""
    from backend import templates
    elig = sorted((e for e in s.ranking if e["eligible"]), key=lambda e: e["rank"])
    ids = [e["district_id"] for e in elig]
    if len(ids) < 6:
        return None
    cut = max(1, round(len(ids) * 25 / 36))
    for key in [k for k, w in sorted(s.weights.items(), key=lambda kv: -kv[1]) if w > 0][:4]:
        if templates._field_rank(ids[0], key, ids)[0] >= cut:
            return key
    return None


def advisor_scenarios() -> list[tuple[str, list[str]]]:
    """20 sessions: the 12 presets, then 8 preset + 'X is critical' setups where the tip rule fires."""
    from backend import advisor, db, templates
    from backend.session import Session
    presets = [p["id"] for p in db.profiles() if p["kind"] == "preset"]
    out = [(p, []) for p in presets]
    for p in presets:
        for f in advisor.QUESTIONS:
            s = Session(id="x")
            s.set_profile(p)
            s.apply_modifiers({f: "critical"})
            if _expected_tip(s) and len(out) < 20:
                out.append((p, [f"{templates.short(f).lower()} is critical"]))
    return out


def run_advisor_scenario(profile: str, setup: list[str]) -> dict:
    from backend import advisor, router, templates
    from backend.session import Session
    s = Session(id="bench")
    name = templates.profile_name(profile)
    log = {"asked": [], "impact_ok": 0, "impact_bad": [], "state_changes_before_apply": 0, "tip_checks": 0,
           "tip_bad": [], "times": []}

    def say(text):
        t0 = time.perf_counter()
        before = dict(s.weights)
        r = router.handle(s, text)
        log["times"].append((time.perf_counter() - t0) * 1000)
        if r["kind"] in ("advisor_answer", "decline_change") and s.weights != before:
            log["state_changes_before_apply"] += 1
        if r["kind"] in ("apply_preset", "adjust", "clarify_answer", "current_preset"):
            log["tip_checks"] += 1
            got = (r["facts"] or {}).get("tip")
            if (got["field"] if got else None) != _expected_tip(s):
                log["tip_bad"].append((text, got, _expected_tip(s)))
        if r["question"]:
            f = r["question"]["field"]
            log["asked"].append(f)
            base = advisor._top_ids(s.weights, s.constraints)
            if max(advisor._impact(base, advisor._what_if(s, f, lab)) for lab in ("high", "low")) > 0:
                log["impact_ok"] += 1
            else:
                log["impact_bad"].append(f)
        return r

    say(f"Apply {name}")
    for t in setup:
        say(t)
    for i in range(8):
        if s.pending_question:
            say(["yes, a lot", "not really", "yes", "not sure"][i % 4])
            if s.pending_change:
                say("Apply this change" if i % 3 else "Keep as is")
        else:
            say(["land is not important", "power matters more", "reset", "water is critical"][i % 4])
    return log


def p9b(client, llm: bool, Gate, pctl) -> list:
    from backend import advisor
    gates = []
    ok, tail = _pytest("tests/test_advisor.py")
    gates.append(Gate(9, "[P9b] advisor unit tests", tail, "all green", ok))
    logs = [run_advisor_scenario(p, setup) for p, setup in advisor_scenarios()]
    n = len(logs)
    asked = sum(len(x["asked"]) for x in logs)
    imp_bad = [f for x in logs for f in x["impact_bad"]]
    gates.append(Gate(9, f"[P9b] questions asked only when an answer changes the top 3 ({n} scenarios)",
                      f"{asked - len(imp_bad)}/{asked} questions, {sum(1 for x in logs if x['asked'])} sessions asked",
                      f"all, {n} scenarios", n == 20 and asked > 0 and not imp_bad))
    rep = [x["asked"] for x in logs if len(x["asked"]) != len(set(x["asked"])) or len(x["asked"]) > 3]
    gates.append(Gate(9, "[P9b] repeated questions / sessions over 3 questions", len(rep) if not rep else f"{len(rep)}: {rep[:2]}",
                      "0", not rep))
    sc = sum(x["state_changes_before_apply"] for x in logs)
    gates.append(Gate(9, "[P9b] state changes before Apply (answer / keep turns)", sc, "0", sc == 0))
    tips = sum(x["tip_checks"] for x in logs)
    tip_bad = [b for x in logs for b in x["tip_bad"]]
    fired = sum(1 for _, setup in advisor_scenarios() if setup)
    gates.append(Gate(9, f"[P9b] tip shown iff the rule holds ({tips} ranking replies, {fired} tip scenarios)",
                      f"{tips - len(tip_bad)}/{tips}" + (f" {tip_bad[:2]}" if tip_bad else ""), "all", not tip_bad))
    for name, file in (("dev", "advisor_answers.jsonl"), ("holdout", "advisor_answers_holdout.jsonl")):
        rows = [json.loads(x) for x in open(ROOT / "eval" / file, encoding="utf-8")]
        up = {"high", "critical"}
        good = sum(1 for r in rows if ((g := advisor.classify_answer(r["text"])) in up) == (r["expect"] in up)
                   and (g in up or g == r["expect"]))
        gates.append(Gate(9, f"[P9b] short-answer lexicon accuracy, {name} ({len(rows)} phrasings)",
                          f"{good / len(rows):.3f}", "≥ 0.95", good / len(rows) >= 0.95,
                          "holdout blind first run 0.800" if name == "holdout" else "—"))
    chips = chip_routing()
    gates.append(Gate(9, "[P9b] follow-up / advisor chips route to the intended kind",
                      f"{chips['ok']}/{chips['n']}" + (f" {chips['bad'][:2]}" if chips["bad"] else ""), "100%",
                      chips["n"] > 0 and not chips["bad"]))
    scripts = [(p, [f"Apply {p}", "yes, a lot", "Apply this change", "compare Pune and Nashik", "not really"])
               for p in ("Automobile", "Dairy", "Textile & Apparel")]
    p95, nt = route_p95(client, pctl, [(None, t) for _, t in scripts])
    gates.append(Gate(9, f"[P9b] /api/message server time p95 with advisor (ms, n={nt})", p95, "< 100", p95 < 100))
    return gates


INTENDED = {"why is": "explain_district", "compare": "compare", "tell me about": "explain_district",
            "apply this change": "adjust", "keep as is": "decline_change"}


def chip_routing() -> dict:
    """Click every follow-up/advisor chip the 20 scenarios produce, each in a copy of its session."""
    import copy
    from backend import router, templates
    from backend.session import Session
    n, bad = 0, []
    for profile, setup in advisor_scenarios():
        s = Session(id="chips")
        for t in [f"Apply {templates.profile_name(profile)}", *setup, "yes, a lot", "tell me about Satara",
                  "compare Pune and Nashik"]:
            r = router.handle(s, t)
            for c in r["chips"]:
                v = c["value"].lower()
                want = next((k for p, k in INTENDED.items() if v.startswith(p)), None)
                if want is None:
                    continue
                trial = copy.deepcopy(s)
                if want != "adjust" and want != "decline_change":
                    trial.pending_question = None
                got = router.handle(trial, c["value"])["kind"]
                n += 1
                if got != want:
                    bad.append((c["value"], got))
    return {"n": n, "ok": n - len(bad), "bad": bad}


# ---------- P9c LLM free-text understanding ----------

def p9c(client, llm: bool, Gate, pctl) -> list:
    from backend import intent
    from eval import intent_eval as ie
    from eval import interpret_eval as ive
    gates = []
    ok, tail = _pytest("tests/test_interpret.py")
    gates.append(Gate(9, "[P9c] interpret unit tests (validation, triggers, mocked endpoint, model choice)", tail,
                      "all green", ok))
    # the fallback never overrides a confident rule: P4 intent accuracy and silent switches unchanged
    res = [x for x in ie.run_corpus(client, ie.load_jsonl(ie.CORPUS)) if "error" not in x]
    test = [x for x in res if x["row"]["split"] == "test"]
    acc = ie.accuracy(test)
    gates.append(Gate(9, f"[P9c] P4 intent accuracy unchanged (held-out {len(test)})", f"{acc:.3f}", "≥ 0.92", acc >= 0.92))
    silent = ie.silent_switches(res)
    gates.append(Gate(9, "[P9c] silent preset switches, P4 corpus", len(silent), "0", not silent))
    rows = ive.load()
    reach = []
    for r in rows:
        sid = _session(client, r.get("profile"))
        if r.get("question"):
            from backend.session import STORE
            STORE.get(sid).pending_question = {**r["question"], "turn": 0}
        m = client.post("/api/message", json={"session_id": sid, "text": r["text"]}).json()
        # an answer the rules already read ("so hardly") is fine too: it is previewed without the LLM
        reach.append("answer" if m["kind"] == "advisor_answer" else m["interpret"]["mode"])
    want = ["answer" if r.get("question") else "industry" for r in rows]
    miss = [r["text"] for r, m, w in zip(rows, reach, want) if m != w]
    gates.append(Gate(9, f"[P9c] suite rows routed to the LLM fallback or read by the rules ({len(rows)})",
                      f"{len(rows) - len(miss)}/{len(rows)}" + (f" {miss[:3]}" if miss else ""), "all", not miss))
    if not llm:
        gates.append(Gate(9, "[P9c] live interpret gates (accuracy, latency)", "not run", "run with --llm", False))
        return gates
    from eval.narration_eval import wait_warm
    wait_warm(client.app)
    warm7 = ive.wait_quality(client.app)
    res = ive.run(client, rows)
    by = {sp: ive.summarize([x for x in res if x["split"] == sp]) for sp in ("dev", "holdout", "answer")}
    model = client.app.state.narrator.quality_model if warm7 else client.app.state.narrator.model
    for sp, target in (("dev", 0.80), ("holdout", 0.75)):
        sm = by[sp]
        gates.append(Gate(9, f"[P9c] industry accuracy, {sp} ({sm['reached']} rows, {model})",
                          f"{sm['accuracy']:.3f}" + (f" wrong {sm['wrong'][:2]}" if sm["wrong"] else ""), f"≥ {target:.2f}",
                          sm["reached"] > 0 and sm["accuracy"] >= target,
                          "dev blind 0.739 (3B); holdout blind 0.913 (7B)" if sp == "holdout" else "3B dev 0.739"))
    sm = by["answer"]
    gates.append(Gate(9, f"[P9c] indirect answers read correctly ({sm['reached']} rows, report; prompt tuned on these)",
                      f"{sm['accuracy']:.3f}", "report", True, "blind 2/3 → 3/7 after routing fixes"))
    allr = [x for x in res if x.get("reached")]
    silent = [x["text"] for x in allr if x.get("silent_change")]
    gates.append(Gate(9, "[P9c] silent state changes after an LLM reading", len(silent), "0", not silent))
    mm = [x["text"] for x in allr if not x.get("message_ok", True)]
    errs = [x["text"] for x in allr if x.get("status") == "error"]
    gates.append(Gate(9, "[P9c] invalid/empty output adds no message; errors", f"{len(mm)} mismatches, {len(errs)} errors",
                      "0, 0", not mm and not errs))
    ms = [x["ms"] for x in allr if "ms" in x]
    p50, p90 = pctl(ms, 50), pctl(ms, 90)
    gates.append(Gate(9, f"[P9c] interpret latency p50 / p90 (ms, n={len(ms)}, {model} warm)", f"{p50} / {p90}",
                      "< 8000 / < 15000", p50 < 8000 and p90 < 15000))
    # the fallback model alone (7B not loaded yet, or on battery): reported
    from backend import config
    icfg = config.load()["interpret"]
    try:
        icfg["prefer_quality_model"] = False
        ind = [r for r in rows if not r.get("question")]
        sm3 = ive.summarize(ive.run(client, ind))
    finally:
        icfg["prefer_quality_model"] = True
    gates.append(Gate(9, f"[P9c] industry accuracy with the 3B model only (dev+holdout, report)",
                      f"{sm3['accuracy']:.3f}, p50 {sm3['p50_ms']} ms", "report", True))
    return gates


# ---------- P9d business brief ----------

BRIEF_PROFILES = ["preset:food_beverages", "preset:automobile", "preset:textile", "preset:pharma", "preset:logistics"]


def run_brief(client, profile: str, setup: list[str] | None = None) -> dict:
    from eval.narration_eval import parse_sse
    from backend import narrate
    from backend.session import STORE
    sid = _session(client, profile)
    for t in setup or []:
        client.post("/api/message", json={"session_id": sid, "text": t})
    t0 = time.perf_counter()
    evs = parse_sse([client.post("/api/brief", json={"session_id": sid}).text])
    total = time.perf_counter() - t0
    done = [d for e, d in evs if e == "section_done"]
    nums = tot = 0
    s = STORE.get(sid)
    for d in done:
        g = d.get("grounding")
        if g:
            nums += g["numbers_supported"]
            tot += g["numbers_total"]
    return {"profile": profile, "s": round(total, 1), "sections": done, "model": next((d for e, d in evs if e == "done"), {}).get("model"),
            "numbers": (nums, tot), "events": [e for e, _ in evs], "cached_key": (s.brief_cache or {}).get("key")}


def brief_with_note(client, delay_s: float = 12.0) -> dict:
    """Start a brief; ~12 s in (inside its first paragraph) ask for a chat note. The lane is released after
    each paragraph, so the note must finish long before the brief does, waiting at most one paragraph."""
    from eval.narration_eval import parse_sse
    sid_b = _session(client, "preset:dairy")
    sid_n = _session(client, "preset:dairy")
    turn = client.post("/api/message", json={"session_id": sid_n, "text": "tell me about Satara"}).json()["turn_id"]
    out: dict = {}

    def brief_thread():
        out["brief"] = parse_sse([client.post("/api/brief", json={"session_id": sid_b}).text])
        out["brief_done"] = time.perf_counter()

    th = threading.Thread(target=brief_thread)
    t0 = time.perf_counter()
    th.start()
    time.sleep(delay_s)
    nr = client.app.state.narrator
    saved = nr.first_token_timeout, nr.total_timeout
    nr.first_token_timeout, nr.total_timeout = 30, 300
    try:
        evs = parse_sse([client.post("/api/narrate", json={"session_id": sid_n, "turn_id": turn}).text])
    finally:
        nr.first_token_timeout, nr.total_timeout = saved
    t_note = time.perf_counter()
    th.join()
    brief_s = out["brief_done"] - t0
    n_par = sum(1 for e, _ in out["brief"] if e == "section_done")
    return {"note": evs[-1][0], "note_wait_s": round((evs[-1][1].get("wait_ms") or 0) / 1000, 1),
            "note_done_s": round(t_note - t0, 1), "brief_s": round(brief_s, 1),
            "paragraph_s": round(brief_s / max(1, n_par), 1)}


def p9d(client, llm: bool, Gate, pctl) -> list:
    from backend import brief
    from backend.session import Session
    gates = []
    ok, tail = _pytest("tests/test_brief.py")
    gates.append(Gate(9, "[P9d] brief unit tests", tail, "all green", ok))
    ms = []
    for p in BRIEF_PROFILES:
        s = Session(id="b")
        s.set_profile(p)
        t0 = time.perf_counter()
        brief.build(s)
        ms.append((time.perf_counter() - t0) * 1000)
    gates.append(Gate(9, "[P9d] deterministic brief sections, build time max (ms, 5 profiles)", round(max(ms), 1), "< 300",
                      max(ms) < 300))
    css = (ROOT / "frontend" / "src" / "styles.css").read_text(encoding="utf-8")
    pr = css[css.index("@media print"):] if "@media print" in css else ""
    ok = "body * { visibility: hidden; }" in pr and ".brief-modal, .brief-modal * { visibility: visible; }" in pr
    gates.append(Gate(9, "[P9d] print stylesheet hides the app and prints only the brief", "yes" if ok else "no", "yes", ok))
    r = router_kinds(["make a business brief", "print a report", "one-pager for my bank"])
    gates.append(Gate(9, "[P9d] brief requests route to the brief", r, "3/3", r == "3/3"))
    if not llm:
        gates.append(Gate(9, "[P9d] live brief gates", "not run", "run with --llm", False))
        return gates
    from eval.narration_eval import wait_warm
    from eval.interpret_eval import wait_quality
    wait_warm(client.app)
    wait_quality(client.app)
    runs = [run_brief(client, p, ["yes, a lot"] if p == "preset:food_beverages" else None) for p in BRIEF_PROFILES]
    shown_ai = [d for x in runs for d in x["sections"] if d["source"] == "ai"]
    bad = [d["id"] for d in shown_ai if not (d.get("grounding") or {}).get("grounded")]
    n_all = sum(len(x["sections"]) for x in runs)
    gates.append(Gate(9, f"[P9d] AI paragraphs shown that pass the groundedness check ({len(shown_ai)} of {n_all} paragraphs AI)",
                      f"{len(shown_ai) - len(bad)}/{len(shown_ai)}", "100%", shown_ai and not bad))
    nums = sum(x["numbers"][0] for x in runs), sum(x["numbers"][1] for x in runs)
    share = nums[0] / nums[1] if nums[1] else 1.0
    gates.append(Gate(9, "[P9d] numeric claims supported in AI paragraphs, before fallback", f"{share:.3f} of {nums[1]}",
                      "≥ 0.97", share >= 0.97))
    model = runs[0]["model"]
    longest = max(x["s"] for x in runs)
    limit = 180 if model and "7b" in model else 90
    gates.append(Gate(9, f"[P9d] full brief time, slowest of 5 (s, {model}, AC)", f"{longest} ({[x['s'] for x in runs]})",
                      f"< {limit}", longest < limit))
    note = brief_with_note(client)
    ok = note["note"] == "done" and note["note_done_s"] < note["brief_s"] and note["note_wait_s"] < 2 * note["paragraph_s"]
    gates.append(Gate(9, "[P9d] a chat note asked 12 s into a brief runs between its paragraphs (30 s note limits)",
                      f"note {note['note']}, waited {note['note_wait_s']} s, done at {note['note_done_s']} s; brief "
                      f"{note['brief_s']} s (~{note['paragraph_s']} s per paragraph)",
                      "done before the brief; wait < 1 paragraph", ok))
    return gates


def router_kinds(texts: list[str]) -> str:
    from backend import router
    from backend.session import Session
    n = 0
    for t in texts:
        s = Session(id="k")
        s.set_profile("preset:dairy")
        n += router.handle(s, t)["kind"] == "brief"
    return f"{n}/{len(texts)}"


# ---------- P9e integration ----------

def p9e(client, llm: bool, Gate, pctl) -> list:
    """Browser walkthrough of the §18 flows (eval/ui_check.js steps 21–25) at both resolutions, headless,
    against a fresh build and its own backend (live Ollama)."""
    from eval import ui_runner
    if not llm:
        return [Gate(9, "[P9e] §18 UI walkthrough (steps 21–25)", "not run", "run with --llm", False)]
    res = ui_runner.run(to=25, frm=21, build=True, timing=False)
    if "error" in res:
        return [Gate(9, "[P9e] §18 UI walkthrough (steps 21–25)", res["error"][:200], "pass", False)]
    gates = []
    for r in res["runs"]:
        failed = [(st["step"], st["detail"][:70]) for st in r.get("steps", []) if not st["pass"]]
        con = r.get("console", []) + r.get("errors", [])
        gates.append(Gate(9, f"[P9e] §18 UI walkthrough steps 21–25 at {r.get('viewport')}, 0 console errors",
                          f"{r.get('passed')}/{r.get('total')}" + (f" {failed}" if failed else "")
                          + (f" console {con[:2]}" if con else ""), "5/5, 0 errors",
                          r.get("passed") == r.get("total") == 5 and not con))
    return gates


STAGES = [p9a, p9b, p9c, p9d, p9e]


def phase9(client, llm: bool, Gate, pctl) -> list:
    gates = []
    for stage in STAGES:
        t0 = time.perf_counter()
        gates.extend(stage(client, llm, Gate, pctl))
        print(f"  {stage.__name__} done in {time.perf_counter() - t0:.0f} s", flush=True)
    return gates
