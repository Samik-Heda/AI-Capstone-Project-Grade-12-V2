"""P9d: the business brief (§18.3). Deterministic content, routing, and the AI sections with a mocked
LLM: grounded paragraphs are kept, ungrounded ones fall back to their template, results are cached."""
import asyncio
import time

import pytest

from backend import brief, db, router
from backend.session import STORE, Session


def sess(profile="preset:dairy") -> Session:
    s = Session(id="b")
    if profile:
        s.set_profile(profile)
    return s


def test_build_is_deterministic_and_complete():
    s = sess()
    t0 = time.perf_counter()
    b = brief.build(s)
    assert (time.perf_counter() - t0) < 0.3
    assert b == brief.build(s)
    assert len(b["districts"]) == 3 and [d["rank"] for d in b["districts"]] == [1, 2, 3]
    assert [x["id"] for x in b["sections"]] == ["summary"] + [f"why_{d['district_id']}" for d in b["districts"]]
    assert all(x["fallback"] for x in b["sections"])
    assert b["priorities"][0]["level"] == "matters most" and b["next_steps"] and b["limitations"]
    status = {f["plain_label"]: f["status"] for f in db.fields()}
    assert all(src["status"] == status[src["label"]] for src in b["sources"])  # from the registry, never hard-coded


def test_build_includes_what_the_user_said():
    s = sess()
    s.business_notes["water"] = "Uses a lot of water"
    s.apply_modifiers({"water": "critical"})
    b = brief.build(s)
    assert "Uses a lot of water" in b["notes"] and b["changes"][0]["field"] == "water"
    assert "The owner said: uses a lot of water." in brief.summary_sheet(s, b).text


def test_state_key_changes_with_priorities():
    s = sess()
    k = brief.state_key(s)
    s.apply_modifiers({"land": "low"})
    assert brief.state_key(s) != k


@pytest.mark.parametrize("text", ["make a business brief", "print a report", "Can I get a one-pager for my bank?",
                                  "summary for my bank"])
def test_brief_route(text):
    s = sess()
    r = router.handle(s, text)
    assert r["kind"] == "brief" and r["facts"]["open_brief"] is True


def test_brief_needs_an_industry():
    r = router.handle(sess(None), "make a business brief")
    assert r["kind"] == "brief" and not (r["facts"] or {}).get("open_brief")


def test_brief_chip_after_apply():
    s = sess(None)
    r = router.handle(s, "Apply Dairy")
    chip = next(c for c in r["chips"] if c["label"] == "Make a business brief")
    assert router.handle(s, chip["value"])["kind"] == "brief"


# ---------- streaming with a mocked LLM ----------

class FakeNarrator:
    quality_model, model, fallback_model = "q", "m", "f"

    def __init__(self, text_for):
        self.text_for, self.calls = text_for, []

    def stream(self, prompt, model, num_predict=None, limits=None):
        self.calls.append((prompt, model, num_predict))
        text = self.text_for(prompt)

        async def gen():
            yield "token", {"t": text}
            yield "done", {"text": text, "model": model, "ms": 1, "first_token_ms": 1, "ollama": {}}
        return gen()


class FakeStatus:
    reachable = True
    models = {"m", "q"}
    _present = staticmethod(lambda name, models: name in models)


def run_events(narr, s):
    from eval.narration_eval import parse_sse

    async def main():
        return [x async for x in brief.events(narr, FakeStatus(), s)]

    return parse_sse(asyncio.run(main()))


def test_events_grounded_kept_ungrounded_replaced(monkeypatch):
    monkeypatch.setattr(brief.power, "battery_mode", lambda: False)
    s = sess()
    top = brief.build(s)["districts"][0]["name"]
    last = [e for e in s.ranking if e["eligible"]][-1]["name"]  # never on the brief's sheets

    def text_for(prompt):
        return f"{last} is the best choice." if "bank manager" in prompt else f"{top} leads this list."

    narr = FakeNarrator(text_for)
    evs = run_events(narr, s)
    kinds = [e for e, _ in evs]
    assert kinds[0] == "brief" and kinds[-1] == "done" and kinds.count("section_done") == 4
    done = {d["id"]: d for e, d in evs if e == "section_done"}
    assert done["summary"]["source"] == "template" and last not in done["summary"]["text"]  # unknown district
    assert done["summary"]["ai_text"]  # kept for the log, not shown
    whys = [v for k, v in done.items() if k != "summary"]
    assert all(v["source"] == "ai" for v in whys)
    assert len(narr.calls) == 4 and {m for _, m, _ in narr.calls} == {"q"}  # 7B on AC; one request per section
    assert all(n == 140 for _, _, n in narr.calls)


def test_events_cached_second_time(monkeypatch):
    monkeypatch.setattr(brief.power, "battery_mode", lambda: False)
    s = sess()
    narr = FakeNarrator(lambda p: "Fine.")
    run_events(narr, s)
    n = len(narr.calls)
    evs = run_events(narr, s)
    assert len(narr.calls) == n and evs[-1] == ("done", {"model": "q", "cached": True})


def test_events_offline_all_templates():
    s = sess()

    class Off(FakeStatus):
        reachable = False

    from eval.narration_eval import parse_sse

    async def main():
        return [x async for x in brief.events(FakeNarrator(lambda p: "x"), Off(), s)]

    evs = parse_sse(asyncio.run(main()))
    done = [d for e, d in evs if e == "section_done"]
    assert len(done) == 4 and all(d["source"] == "template" and d["text"] for d in done)


def test_events_battery_uses_small_model(monkeypatch):
    monkeypatch.setattr(brief.power, "battery_mode", lambda: True)
    monkeypatch.setattr(brief.narrate.power, "battery_mode", lambda: True)
    narr = FakeNarrator(lambda p: "Fine.")
    run_events(narr, sess())
    assert {m for _, m, _ in narr.calls} == {"m"}  # FakeStatus has no fallback model installed


def test_brief_endpoint_needs_profile(client):
    from eval.narration_eval import parse_sse
    sid = client.post("/api/session").json()["session_id"]
    evs = parse_sse([client.post("/api/brief", json={"session_id": sid}).text])
    assert evs[0][0] == "error"


def test_brief_endpoint_offline_streams_templates(client, monkeypatch):
    from eval.narration_eval import parse_sse
    monkeypatch.setattr(brief, "pick_brief_model", lambda narrator, status: None)  # as if Ollama were offline
    sid = client.post("/api/session").json()["session_id"]
    client.post(f"/api/session/{sid}/profile", json={"profile_id": "preset:textile"})
    evs = parse_sse([client.post("/api/brief", json={"session_id": sid}).text])
    assert evs[0][0] == "brief" and evs[-1][0] == "done"
    assert STORE.get(sid).brief_cache["model"] is None
