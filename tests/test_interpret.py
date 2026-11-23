"""P9c: LLM free-text understanding (§18.1). The LLM is mocked: these tests check when it is asked, how
its output is validated, and that nothing changes before Apply."""
import pytest

from backend import interpret, router
from backend.session import STORE, Session


# ---------- validation ----------

@pytest.mark.parametrize("parsed,want", [
    ({"industry": "dairy"}, "preset:dairy"),
    ({"industry": "agro_cold", "extra": 1}, "preset:agro_cold"),
    ({"industry": "unknown"}, None),
    ({"industry": "Dairy"}, None),            # names are not keys
    ({"industry": "pharmaceuticals"}, None),
    ({"industry": "cement"}, None),
    ({"industry": 3}, None),
    ({}, None), (None, None), ([], None), ("dairy", None),
])
def test_validate_industry(parsed, want):
    assert interpret.validate_industry(parsed) == want


@pytest.mark.parametrize("parsed,want", [
    ({"answer": "a_lot"}, "critical"), ({"answer": "yes"}, "high"), ({"answer": "no"}, "low"),
    ({"answer": "unsure"}, "unsure"), ({"answer": "not_an_answer"}, None), ({"answer": "maybe"}, None),
    ({"answer": None}, None), (None, None),
])
def test_validate_answer(parsed, want):
    assert interpret.validate_answer(parsed) == want


def test_industry_prompt_lists_all_presets_and_schema_matches():
    keys = set(interpret.preset_keys())
    assert len(keys) == 12 and keys == set(interpret.COVERS)
    assert set(interpret.industry_schema()["properties"]["industry"]["enum"]) == keys | {"unknown"}
    for k in keys:
        assert f"{k} = " in interpret.industry_system()


# ---------- when the router asks ----------

def flag(text: str, profile: str | None = None, s: Session | None = None) -> dict:
    s = s or Session(id="t")
    if profile:
        s.set_profile(profile)
    return router.handle(s, text, narration_ready=True)


@pytest.mark.parametrize("text", ["we wash vegetables all day so we need loads of water",
                                  "something with bamboo sticks for incense, my uncle does it"])
def test_rules_give_up_asks_llm(text):
    r = flag(text)
    assert r["interpret"] == {"available": True, "mode": "industry"}, r["kind"]


@pytest.mark.parametrize("text", ["dairy plant", "textile mill, water is critical", "compare Pune and Nashik",
                                  "I want to open a hospital", "cotton ginning", "cement plant", "hello", "reset",
                                  "What is the GDP of Pune?"])
def test_confident_or_refused_never_asks_llm(text):
    assert flag(text)["interpret"]["available"] is False


def test_not_available_when_model_offline():
    s = Session(id="t")
    r = router.handle(s, "we wash vegetables all day so we need loads of water", narration_ready=False)
    assert r["interpret"]["available"] is False


def test_unreadable_answer_reasks_once_and_asks_llm():
    s = Session(id="t")
    s.set_profile("preset:dairy")
    s.pending_question = {"field": "water", "text": "Will your plant use a lot of water?", "turn": 0}
    r = router.handle(s, "the process is dry", narration_ready=True)
    assert r["kind"] == "advisor_reask" and r["interpret"] == {"available": True, "mode": "answer"}
    assert s.pending_question["field"] == "water" and r["chips"][0]["value"] == "Yes, a lot"
    r = router.handle(s, "hmm the process is dry", narration_ready=True)  # second miss: dropped, no loop
    assert r["kind"] != "advisor_reask" and s.pending_question is None


@pytest.mark.parametrize("text", ["show me all 36", "which district is best?", "tell me about Satara"])
def test_requests_are_not_read_as_answers(text):
    s = Session(id="t")
    s.set_profile("preset:dairy")
    s.pending_question = {"field": "water", "text": "Will your plant use a lot of water?", "turn": 0}
    r = router.handle(s, text, narration_ready=True)
    assert r["kind"] != "advisor_reask" and r["interpret"]["mode"] != "answer"


# ---------- the endpoint (mocked LLM) ----------

@pytest.fixture
def mock_llm(client, monkeypatch):
    st, nr = client.app.state.ollama, client.app.state.narrator
    monkeypatch.setattr(st, "reachable", True)
    monkeypatch.setattr(st, "models", {nr.model, nr.fallback_model})
    calls = []
    box = {"out": ({"industry": "agro_cold"}, {"ms": 5, "model": nr.model})}

    async def fake(system, user, schema, model, num_predict, timeout):
        calls.append({"system": system, "user": user, "schema": schema})
        return box["out"]

    monkeypatch.setattr(nr, "complete_json", fake)
    return box, calls


def _sid(client, profile=None):
    sid = client.post("/api/session").json()["session_id"]
    if profile:
        client.post(f"/api/session/{sid}/profile", json={"profile_id": profile})
    return sid


def test_endpoint_suggests_and_changes_nothing_until_apply(client, mock_llm):
    box, calls = mock_llm
    sid = _sid(client)
    m = client.post("/api/message", json={"session_id": sid, "text": "we wash vegetables all day so we need loads of water"}).json()
    assert m["interpret"]["available"]
    r = client.post("/api/interpret", json={"session_id": sid, "turn_id": m["turn_id"]}).json()
    assert r["status"] == "suggested" and r["result"] == "preset:agro_cold"
    msg = r["message"]
    assert msg["kind"] == "suggest_preset" and msg["chips"][0]["value"] == "Apply Agro & Cold Storage"
    assert msg["understood"]["industry"]["tier"] == "llm"
    assert msg["state"]["profile"] is None and msg["state"]["pending_suggest"]["modifiers"] == {"water": "high"}
    assert calls[0]["user"].startswith("we wash vegetables")
    a = client.post("/api/message", json={"session_id": sid, "text": "Apply Agro & Cold Storage"}).json()
    assert a["state"]["profile"]["id"] == "preset:agro_cold" and a["state"]["modifiers"] == {"water": "high"}


def test_endpoint_runs_once_per_turn(client, mock_llm):
    sid = _sid(client)
    m = client.post("/api/message", json={"session_id": sid, "text": "we wash vegetables all day so we need loads of water"}).json()
    client.post("/api/interpret", json={"session_id": sid, "turn_id": m["turn_id"]})
    r = client.post("/api/interpret", json={"session_id": sid, "turn_id": m["turn_id"]}).json()
    assert r["status"] == "none" and r["message"] is None


@pytest.mark.parametrize("out,status", [
    (({"industry": "unknown"}, {"ms": 5}), "none"),
    (({"industry": "steel"}, {"ms": 5}), "none"),
    ((None, {"error": "timeout"}), "error"),
    ((None, {"error": "offline"}), "error"),
])
def test_endpoint_invalid_or_failed_adds_nothing(client, mock_llm, out, status):
    box, _ = mock_llm
    box["out"] = out
    sid = _sid(client)
    m = client.post("/api/message", json={"session_id": sid, "text": "we wash vegetables all day so we need loads of water"}).json()
    n_turns = len(STORE.get(sid).history)
    r = client.post("/api/interpret", json={"session_id": sid, "turn_id": m["turn_id"]}).json()
    assert r["status"] == status and r["message"] is None and len(STORE.get(sid).history) == n_turns
    assert STORE.get(sid).pending_suggest["preset_id"] == "preset:general_manufacturing"  # the rules' offer stands


def test_endpoint_stale_turn_is_ignored(client, mock_llm):
    sid = _sid(client)
    m = client.post("/api/message", json={"session_id": sid, "text": "we wash vegetables all day so we need loads of water"}).json()
    client.post("/api/message", json={"session_id": sid, "text": "compare Pune and Nashik"})  # user moved on
    r = client.post("/api/interpret", json={"session_id": sid, "turn_id": m["turn_id"]}).json()
    assert r["message"] is None


def test_endpoint_answer_mode_previews(client, mock_llm):
    box, calls = mock_llm
    box["out"] = ({"answer": "no"}, {"ms": 5})
    sid = _sid(client, "preset:dairy")
    s = STORE.get(sid)
    s.pending_question = {"field": "water", "text": "Will your plant use a lot of water?", "turn": 0}
    before = dict(s.weights)
    m = client.post("/api/message", json={"session_id": sid, "text": "the process is dry"}).json()
    assert m["interpret"]["mode"] == "answer", (m["kind"], m["reply_md"])
    r = client.post("/api/interpret", json={"session_id": sid, "turn_id": m["turn_id"]}).json()
    assert r["status"] == "suggested" and r["message"]["kind"] == "advisor_answer"
    assert "QUESTION: Will your plant use a lot of water?" in calls[-1]["user"]
    assert s.pending_change == {"modifiers": {"water": "low"}} and s.weights == before
    assert s.business_notes["water"] == "Uses little water" and s.pending_question is None


def test_unknown_turn_404(client):
    sid = _sid(client)
    assert client.post("/api/interpret", json={"session_id": sid, "turn_id": "nope"}).status_code == 404


# ---------- model choice (§18.1: quality model only when already loaded, on AC) ----------

class _St:
    def __init__(self, models, loaded, wants=True):
        self.models, self.loaded, self._wants, self.reachable = set(models), set(loaded), wants, True

    _present = staticmethod(lambda name, models: name in models)

    @property
    def wants_quality(self):
        return self._wants

    @property
    def quality_warm(self):
        return "qwen2.5:7b-instruct" in self.loaded


def test_interpret_uses_quality_model_only_when_warm():
    from backend import narrate
    n = narrate.Narrator.from_config()
    every = {n.model, n.fallback_model, n.quality_model}
    assert interpret.pick_interpret_model(n, _St(every, {n.quality_model})) == n.quality_model
    assert interpret.pick_interpret_model(n, _St(every, set())) == n.model            # cold 7B: never wait for it
    assert interpret.pick_interpret_model(n, _St(every, {n.quality_model}, wants=False)) == n.model  # battery/off


def test_health_loop_warms_quality_after_chat_model(monkeypatch):
    import asyncio
    from backend import ollama_health
    st = ollama_health.OllamaStatus()
    calls = []

    async def fake_check():
        st.reachable, st.models = True, {st.narration_model, st.quality_model}
        st.loaded = {st.narration_model}
        st.warm = True

    async def on_q():
        calls.append("q")

    monkeypatch.setattr(st, "check", fake_check)
    monkeypatch.setattr(ollama_health.power, "battery_mode", lambda: False)
    st.on_quality_cold, st.interval = on_q, 0.01

    async def main():
        st.start()
        await asyncio.sleep(0.05)
        await st.stop()

    asyncio.run(main())
    assert calls  # the quality model was asked to load once the chat model was warm


def test_reask_clears_stale_suggestion_so_yes_answers_the_question():
    s = Session(id="t")
    s.set_profile("preset:food_beverages")
    s.pending_question = {"field": "power", "text": "Will your plant run heavy machines?", "turn": 0}
    r = router.handle(s, "our machines run three shifts", narration_ready=True)
    assert r["kind"] == "advisor_reask" and s.pending_suggest is None
    r = router.handle(s, "yes")
    assert r["kind"] == "advisor_answer" and s.pending_change == {"modifiers": {"power": "high"}}
    assert s.profile_id == "preset:food_beverages"


def test_unreadable_description_with_an_industry_on_asks_llm():
    r = flag("washing powder and dish bars", "preset:food_beverages")
    assert r["kind"] == "chat" and r["interpret"] == {"available": True, "mode": "industry"}


@pytest.mark.parametrize("text", ["show me all 36", "which district is best?", "what about the top five",
                                  "tell me more"])
def test_chat_questions_with_an_industry_on_do_not_ask_llm(text):
    assert flag(text, "preset:food_beverages")["interpret"]["available"] is False
