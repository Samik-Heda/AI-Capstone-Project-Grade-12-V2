"""Phase 5: scope guard, fact sheets, groundedness checker, streaming and the /narrate endpoint.
No test here needs Ollama: the stream is exercised against httpx.MockTransport."""
import asyncio
import json

import httpx
import pytest

from backend import db, narrate, scope, scoring, templates
from backend.session import STORE

SPEC_OUT_OF_SCOPE = ["What is the GDP of Pune?", "Which district has the best ports in Gujarat?",
                     "What's the weather in Nagpur?", "Write a poem about Nashik", "Who is the chief minister?",
                     "Will land prices rise next year?"]
IN_SCOPE = ["water is critical", "land cost does not matter", "compare Pune and Nashik", "why is Sangli below Kolhapur?",
            "we will set up a dairy plant", "I am going to build a textile mill", "a plant in 2027 near a port",
            "third party logistics warehouse", "who leads for textiles?", "export oriented unit", "food processing plant"]


def ids() -> dict[str, int]:
    return {d["name"]: d["id"] for d in db.districts()}


def ranked(pid: str):
    p = scoring.load_profile(pid)
    res = scoring.rank(p["weights"], p["constraints"])
    return res["ranking"], res["weights"], p["name"]


# ---------- scope guard ----------

@pytest.mark.parametrize("text", SPEC_OUT_OF_SCOPE)
def test_spec_out_of_scope_refused(text):
    assert scope.check(text).out_of_scope


@pytest.mark.parametrize("text", IN_SCOPE)
def test_in_scope_not_refused(text):
    assert not scope.check(text).out_of_scope, scope.check(text)


def test_refusal_changes_no_state(client):
    sid = client.post("/api/session").json()["session_id"]
    client.post(f"/api/session/{sid}/profile", json={"profile_id": "preset:sugar_ethanol"})
    before = client.get(f"/api/session/{sid}").json()["state"]
    for text in SPEC_OUT_OF_SCOPE:
        r = client.post("/api/message", json={"session_id": sid, "text": text}).json()
        assert r["kind"] == "refusal"
        assert r["reply_md"] == templates.REFUSAL
        assert r["narration"]["available"] is False
    after = client.get(f"/api/session/{sid}").json()["state"]
    assert after["weights"] == before["weights"] and after["modifiers"] == before["modifiers"]


# ---------- fact sheets ----------

def test_sheets_within_budget_for_every_district_and_profile():
    with db.connect() as con:
        pids = [r[0] for r in con.execute("SELECT id FROM profile")]
    for pid in pids:
        rk, w, name = ranked(pid)
        elig = [e["district_id"] for e in rk if e["eligible"]]
        for e in rk:
            sh = narrate.explain_sheet(e["district_id"], rk, w, name)
            assert sh.tokens <= narrate.MAX_SHEET_TOKENS
        if len(elig) >= 2:
            assert narrate.compare_sheet(elig[0], elig[1], rk, w, name).tokens <= narrate.MAX_SHEET_TOKENS
            assert narrate.why_sheet(elig[1], elig[0], rk, w, name).tokens <= narrate.MAX_SHEET_TOKENS
        assert narrate.chat_sheet(rk, w, [], name, "which district is best?").tokens <= narrate.MAX_SHEET_TOKENS


def test_explain_sheet_numbers_come_from_facts():
    rk, w, name = ranked("preset:sugar_ethanol")
    top = min((e for e in rk if e["eligible"]), key=lambda e: e["rank"])
    sh = narrate.explain_sheet(top["district_id"], rk, w, name)
    assert f"{top['score']:.1f}" in sh.text and "#1 of" in sh.text
    assert sh.pair and sh.pair["a"] == top["district_id"]


def test_compare_sheet_leaders_match_facts():
    rk, w, name = ranked("preset:sugar_ethanol")
    i = ids()
    sh = narrate.compare_sheet(i["Kolhapur"], i["Sangli"], rk, w, name)
    from backend import facts
    cmp = facts.compare(i["Kolhapur"], i["Sangli"], rk, w)
    for r in cmp["rows"]:
        want = {"a": i["Kolhapur"], "b": i["Sangli"], "tie": None}[r["leader"]]
        assert sh.pair["leaders"][r["field"]] == want


def test_why_question_on_compare_uses_why_sheet(client):
    sid = client.post("/api/session").json()["session_id"]
    client.post(f"/api/session/{sid}/profile", json={"profile_id": "preset:sugar_ethanol"})
    m = client.post("/api/message", json={"session_id": sid, "text": "why is Sangli below Kolhapur?"}).json()
    turn = STORE.get(sid).find_turn(m["turn_id"])
    assert narrate.build_sheet(turn.ctx).task == "why_not_first"
    m = client.post("/api/message", json={"session_id": sid, "text": "compare Sangli and Kolhapur"}).json()
    assert narrate.build_sheet(STORE.get(sid).find_turn(m["turn_id"]).ctx).task == "compare"


# ---------- groundedness checker ----------

@pytest.fixture(scope="module")
def ks_sheet():
    rk, w, name = ranked("preset:sugar_ethanol")
    i = ids()
    return narrate.compare_sheet(i["Kolhapur"], i["Sangli"], rk, w, name), rk


def _leader_field(sheet, who: int) -> str:
    return next(f for f, d in sheet.pair["leaders"].items() if d == who and f in ("water", "crop_output", "estates"))


def test_grounded_numbers_with_rounding(ks_sheet):
    sh, rk = ks_sheet
    k = next(e for e in rk if e["name"] == "Kolhapur")
    out = f"Kolhapur scores {k['score']:.1f} and leads overall. It is about {round(k['score'])} out of 100."
    g = narrate.check(out, sh)
    assert g["unsupported_numbers"] == [] and g["numbers_total"] == 3


def test_invented_number_is_unsupported(ks_sheet):
    sh, _ = ks_sheet
    g = narrate.check("Kolhapur has 4,213 factories.", sh)
    assert not g["grounded"] and g["unsupported_numbers"] == ["4,213"]


def test_unknown_district_flagged(ks_sheet):
    sh, _ = ks_sheet
    g = narrate.check("Kolhapur is a better choice than Latur.", sh)
    assert not g["grounded"] and g["unknown_districts"] == ["Latur"]


def test_direction_claims(ks_sheet):
    sh, _ = ks_sheet
    i = ids()
    kf = _leader_field(sh, i["Kolhapur"])
    sf = _leader_field(sh, i["Sangli"])
    lab = {f: templates.short(f).lower() for f in (kf, sf)}
    ok = f"Kolhapur has better {lab[kf]} than Sangli, while Sangli leads on {lab[sf]}."
    assert narrate.check(ok, sh)["wrong_directions"] == []
    bad = f"Sangli has better {lab[kf]} than Kolhapur."
    assert narrate.check(bad, sh)["wrong_directions"]
    bad2 = f"Kolhapur trails Sangli on {lab[kf]}."
    assert narrate.check(bad2, sh)["wrong_directions"]
    wrong_overall = "Sangli scores higher overall than Kolhapur."
    assert narrate.check(wrong_overall, sh)["wrong_directions"]
    # a factor named as the other side's strength in a 'despite' clause is not a claim
    fine = f"Kolhapur leads by a small margin, despite Sangli's {lab[sf]}."
    assert narrate.check(fine, sh)["wrong_directions"] == []


def test_weighting_remarks_are_not_direction_claims(ks_sheet):
    sh, _ = ks_sheet
    i = ids()
    sf = _leader_field(sh, i["Sangli"])
    lab = templates.short(sf).lower()
    assert narrate.check(f"Sangli is better on {lab}, which carries less weight.", sh)["wrong_directions"] == []
    assert narrate.check(f"Sangli's {lab} counts for less.", sh)["wrong_directions"] == []


def test_pronoun_sentence_continues_subject(ks_sheet):
    sh, _ = ks_sheet
    i = ids()
    kf = _leader_field(sh, i["Kolhapur"])
    lab = templates.short(kf).lower()
    assert narrate.check(f"Sangli ranks third. It lags behind Kolhapur on {lab}.", sh)["wrong_directions"] == []
    assert narrate.check(f"Kolhapur ranks second. It lags behind Sangli on {lab}.", sh)["wrong_directions"]


def test_object_and_relative_owners(ks_sheet):
    sh, _ = ks_sheet
    i = ids()
    kf, sf = _leader_field(sh, i["Kolhapur"]), _leader_field(sh, i["Sangli"])
    k, s_ = templates.plain_lc(kf), templates.plain_lc(sf)
    ok = [f"Kolhapur is ahead of Sangli's 81.7 by 1.3 points, with an edge in {k}.",
          f"Kolhapur's gains outweigh the {s_} advantage of Sangli.",
          f"Sangli trails on {k} compared to Kolhapur, which has better {k}."]
    for text in ok:
        assert narrate.check(text, sh)["wrong_directions"] == [], text
    assert narrate.check(f"Kolhapur's gains outweigh the {k} advantage of Sangli.", sh)["wrong_directions"]
    # a purpose phrase is not a compared factor
    assert narrate.check(f"Sangli is better on {s_} for agriculture.", sh)["wrong_directions"] == []


def test_pronoun_before_mention_keeps_subject(ks_sheet):
    """'…, and it lags behind the leader, Kolhapur, on X': 'it' is still the previous sentence's district."""
    sh, _ = ks_sheet
    i = ids()
    lab = templates.plain_lc(_leader_field(sh, i["Kolhapur"]))
    good = f"Sangli ranks second. This is due to weak scores, and it lags behind the leader, Kolhapur, on {lab}."
    bad = f"Kolhapur ranks first. This is due to strong scores, and it lags behind the runner-up, Sangli, on {lab}."
    assert narrate.check(good, sh)["wrong_directions"] == []
    assert narrate.check(bad, sh)["wrong_directions"]


def test_coordinated_cue_does_not_take_earlier_factors(ks_sheet):
    """', and outperforming Sangli in X' claims X only, not the factors listed before ', and'."""
    sh, _ = ks_sheet
    i = ids()
    kf, sf = _leader_field(sh, i["Kolhapur"]), _leader_field(sh, i["Sangli"])
    k, s_ = templates.plain_lc(kf), templates.plain_lc(sf)
    assert narrate.check(f"Kolhapur is first, driven by its {s_}, and outperforming Sangli in {k}.", sh)[
        "wrong_directions"] == []
    assert narrate.check(f"Kolhapur is first, driven by its {k}, and outperforming Sangli in {s_}.", sh)[
        "wrong_directions"]


def test_relative_clause_owns_later_cues_and_the_district_continues_subject(ks_sheet):
    """'The district lags behind Kolhapur, which scores higher and is ahead in X': Kolhapur owns
    'ahead' (an earlier cue in the relative clause does not break it), 'the district' is Sangli."""
    sh, _ = ks_sheet
    i = ids()
    k = templates.plain_lc(_leader_field(sh, i["Kolhapur"]))
    s_ = templates.plain_lc(_leader_field(sh, i["Sangli"]))
    good = f"Sangli ranks second. The district lags behind Kolhapur, which has a better score and is ahead in {k}."
    bad = f"Sangli ranks second. The district lags behind Kolhapur, which has a better score and is ahead in {s_}."
    assert narrate.check(good, sh)["wrong_directions"] == []
    assert narrate.check(bad, sh)["wrong_directions"]


def test_ahead_of_names_the_other_side(ks_sheet):
    """'X is ahead of Y on F' claims X leads on F ('the F advantage of Y' is the noun form)."""
    sh, _ = ks_sheet
    i = ids()
    k = templates.plain_lc(_leader_field(sh, i["Kolhapur"]))
    assert narrate.check(f"Kolhapur is ahead of Sangli on {k}.", sh)["wrong_directions"] == []
    assert narrate.check(f"Sangli is ahead of Kolhapur on {k}.", sh)["wrong_directions"]


def test_object_only_clause_keeps_sentence_subject(ks_sheet):
    """'Sangli, while better on X, scores less, slightly below Kolhapur': the clause after ', while' names
    Kolhapur only as an object, so Sangli (the sentence subject) owns 'better' and 'below'."""
    sh, _ = ks_sheet
    i = ids()
    k = templates.plain_lc(_leader_field(sh, i["Kolhapur"]))
    s_ = templates.plain_lc(_leader_field(sh, i["Sangli"]))
    assert narrate.check(f"Sangli, while better on {s_}, scores less, slightly below Kolhapur.", sh)[
        "wrong_directions"] == []
    assert narrate.check(f"Sangli, while better on {k}, scores less, slightly below Kolhapur.", sh)["wrong_directions"]
    assert narrate.check(f"Kolhapur, while better on {k}, scores less, slightly below Sangli.", sh)["wrong_directions"]


def test_land_price_wording_flips_direction():
    rk, w, name = ranked("preset:sugar_ethanol")
    i = ids()
    sh = narrate.compare_sheet(i["Kolhapur"], i["Sangli"], rk, w, name)
    land_leader = sh.pair["leaders"]["land"]
    cheaper = "Sangli" if land_leader == i["Sangli"] else "Kolhapur"
    other = "Kolhapur" if cheaper == "Sangli" else "Sangli"
    assert narrate.check(f"{other} has more expensive land than {cheaper}.", sh)["wrong_directions"] == []
    assert narrate.check(f"{cheaper} has more expensive land than {other}.", sh)["wrong_directions"]


def test_refusal_sentence_is_grounded(ks_sheet):
    sh, _ = ks_sheet
    g = narrate.check("I can only answer from this tool's district data.", sh)
    assert g["grounded"] and g["refusal"]


# ---------- streaming client (mock Ollama) ----------

def _ndjson(*parts: str, done: bool = True) -> bytes:
    lines = [json.dumps({"message": {"content": p}, "done": False}) for p in parts]
    if done:
        lines.append(json.dumps({"message": {"content": ""}, "done": True, "eval_count": len(parts),
                                 "prompt_eval_count": 50, "prompt_eval_duration": 1_000_000, "load_duration": 0}))
    return ("\n".join(lines) + "\n").encode()


def _narrator(handler) -> narrate.Narrator:
    n = narrate.Narrator.from_config()
    n.client = httpx.AsyncClient(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    return n


async def _collect(n: narrate.Narrator, prompt: str = "x") -> list[tuple[str, dict]]:
    out = [ev async for ev in n.stream(prompt, n.model)]
    await n.aclose()
    return out


def test_stream_tokens_then_done():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=_ndjson("Nashik ", "leads."))

    evs = asyncio.run(_collect(_narrator(handler)))
    assert [e for e, _ in evs] == ["token", "token", "done"]
    assert evs[-1][1]["text"] == "Nashik leads." and evs[-1][1]["ollama"]["prompt_tokens"] == 50
    body = seen["body"]
    assert body["stream"] is True and body["keep_alive"] == "30m"
    assert body["options"] == {"num_ctx": 2048, "num_predict": 120, "temperature": 0.2, "top_p": 0.9}
    assert body["messages"][0] == {"role": "system", "content": narrate.SYSTEM_PROMPT}


def test_stream_first_token_timeout():
    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(5)
            yield _ndjson("late")

    n = _narrator(lambda req: httpx.Response(200, stream=Slow()))
    n.first_token_timeout = 0.2
    evs = asyncio.run(_collect(n))
    assert evs == [("error", {"message": "AI note unavailable (timeout)", "partial": ""})]


def test_stream_total_timeout_keeps_partial():
    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield _ndjson("Nashik", done=False)
            await asyncio.sleep(5)
            yield _ndjson(" late")

    n = _narrator(lambda req: httpx.Response(200, stream=Trickle()))
    n.total_timeout = 0.3
    evs = asyncio.run(_collect(n))
    assert evs[0] == ("token", {"t": "Nashik"})
    assert evs[-1][0] == "error" and "timeout" in evs[-1][1]["message"]


def test_stream_connection_refused_is_offline_event():
    def handler(req):
        raise httpx.ConnectError("refused")

    evs = asyncio.run(_collect(_narrator(handler)))
    assert evs == [("error", {"message": "AI note unavailable (offline)"})]


def test_stream_http_error_is_event():
    evs = asyncio.run(_collect(_narrator(lambda req: httpx.Response(404, json={"error": "model not found"}))))
    assert evs[0][0] == "error" and "404" in evs[0][1]["message"]


# ---------- /api/narrate ----------

def _events(text: str) -> list[tuple[str, dict]]:
    from eval.narration_eval import parse_sse
    return parse_sse([text])


def test_sse_parser_buffers_partial_chunks():
    from eval.narration_eval import parse_sse
    raw = narrate.sse("token", {"t": "Na"}) + narrate.sse("token", {"t": "shik"}) + narrate.sse("done", {"ok": 1})
    chunks = [raw[i:i + 7] for i in range(0, len(raw), 7)]
    assert parse_sse(chunks) == [("token", {"t": "Na"}), ("token", {"t": "shik"}), ("done", {"ok": 1})]


def test_narrate_unknown_turn_404(client):
    sid = client.post("/api/session").json()["session_id"]
    assert client.post("/api/narrate", json={"session_id": sid, "turn_id": "nope"}).status_code == 404


def test_narrate_offline_is_error_event_not_500(client, monkeypatch):
    st = client.app.state.ollama
    monkeypatch.setattr(st, "reachable", False)
    sid = client.post("/api/session").json()["session_id"]
    client.post(f"/api/session/{sid}/profile", json={"profile_id": "preset:automobile"})
    m = client.post("/api/message", json={"session_id": sid, "text": "compare Pune and Nashik"}).json()
    assert m["narration"]["available"] is False and m["reply_md"]
    r = client.post("/api/narrate", json={"session_id": sid, "turn_id": m["turn_id"]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert _events(r.text) == [("error", {"message": "AI note unavailable (offline)"})]


def test_narrate_streams_and_grounds_with_mock_ollama(client, monkeypatch):
    st, nr = client.app.state.ollama, client.app.state.narrator
    monkeypatch.setattr(st, "reachable", True)
    monkeypatch.setattr(st, "models", {nr.model})
    captured = {}

    async def fake_stream(prompt, model):
        captured["prompt"] = prompt
        yield "token", {"t": "Pune leads overall."}
        yield "done", {"text": "Pune leads overall.", "model": model, "ms": 5, "first_token_ms": 1, "ollama": {}}

    monkeypatch.setattr(nr, "stream", fake_stream)
    sid = client.post("/api/session").json()["session_id"]
    client.post(f"/api/session/{sid}/profile", json={"profile_id": "preset:automobile"})
    m = client.post("/api/message", json={"session_id": sid, "text": "compare Pune and Nashik"}).json()
    assert m["narration"]["available"] is True
    evs = _events(client.post("/api/narrate", json={"session_id": sid, "turn_id": m["turn_id"]}).text)
    assert evs[0] == ("token", {"t": "Pune leads overall."})
    done = evs[-1][1]
    assert evs[-1][0] == "done" and done["task"] == "compare" and "grounded" in done["grounding"]
    assert captured["prompt"].startswith("FACTS:")
    assert STORE.get(sid).find_turn(m["turn_id"]).narration["text"] == "Pune leads overall."


def test_non_narrated_kinds_have_no_context(client):
    sid = client.post("/api/session").json()["session_id"]
    for text in ("help", "cement plant"):
        m = client.post("/api/message", json={"session_id": sid, "text": text}).json()
        assert STORE.get(sid).find_turn(m["turn_id"]).ctx is None
        evs = _events(client.post("/api/narrate", json={"session_id": sid, "turn_id": m["turn_id"]}).text)
        assert evs[0][0] == "error"


def test_cue_before_for_still_compares_the_factor():
    """compare-8 false positive (2026-10-04): 'scores higher for cheap land' is a land claim, not an overall one."""
    i = ids()
    rk, w, prof = ranked("preset:textile")
    sheet = narrate.compare_sheet(i["Amravati"], i["Nagpur"], rk, w, prof)
    g = narrate.check("Amravati scores higher for cheap land, while Nagpur excels in industrial areas and "
                      "reliable electricity for textile and apparel.", sheet)
    assert g["wrong_directions"] == []
    g = narrate.check("Nagpur scores higher for cheap land.", sheet)
    assert g["wrong_directions"]  # the opposite claim is still caught
