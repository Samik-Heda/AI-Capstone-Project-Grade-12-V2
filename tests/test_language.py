"""Phase 4: text handling, modifier parser, industry resolver, router, sessions and profile CRUD."""
import pytest

from backend import intent, modifiers, nlp, scoring
from backend.embeddings import INDEX


@pytest.fixture
def offline(monkeypatch):
    """Embedding tier off, as when Ollama is down."""
    monkeypatch.setattr(INDEX, "enabled", False)


def resolve(text, **kw):
    toks = nlp.tokenize(text)
    return intent.resolve(text, toks, nlp.term_matcher().find(toks), **kw)


def say(client, sid, text):
    r = client.post("/api/message", json={"session_id": sid, "text": text})
    assert r.status_code == 200, r.text
    return r.json()


def session(client, profile=None):
    sid = client.post("/api/session").json()["session_id"]
    if profile:
        assert client.post(f"/api/session/{sid}/profile", json={"profile_id": profile}).status_code == 200
    return sid


# ---------- nlp ----------

def test_normalize_expands_negation():
    assert nlp.normalize("Don’t  care") == "do not care"
    assert nlp.normalize("doesn't matter") == "does not matter"


@pytest.mark.parametrize("tok,lem", [("textiles", "textil"), ("textile", "textil"), ("boxes", "box"), ("batteries", "battery"),
                                     ("cheeses", "chees"), ("glass", "glass"), ("caring", "caring"),
                                     ("weaving", "weav"), ("tomatoes", "tomato"), ("bottles", "bottl"),
                                     ("bottling", "bottl"), ("flavoured", "flavour")])
def test_lemma(tok, lem):
    assert nlp.lemma(tok) == lem


def test_no_substring_matches():
    toks = nlp.tokenize("careful planning near the airport; important support")
    kinds = {m.kind for m in nlp.term_matcher().find(toks)}
    assert "industry" not in kinds and "field" not in kinds


# ---------- modifiers (§7.4) ----------

@pytest.mark.parametrize("text,expected", [
    ("literacy is critical", {"literacy": "critical"}),
    ("make port proximity highly critical", {"port": "critical"}),
    ("water matters a lot", {"water": "high"}),
    ("slightly more weight on rail", {"rail": "high"}),
    ("ignore crop output", {"crop_output": "exclude"}),
    ("land cost doesn't matter", {"land": "low"}),
    ("water is not important", {"water": "low"}),
    ("I don't care about irrigation", {"irrigation": "low"}),
    ("power and water are both critical", {"power": "critical", "water": "critical"}),
    ("less emphasis on highways", {"highway": "low"}),
    ("need cheap land", {"land": "high"}),
    ("expensive land is fine", {"land": "low"}),
    ("good rail connectivity required", {"rail": "high"}),
    ("literacy is critical but ignore crop", {"literacy": "critical", "crop_output": "exclude"}),
    ("power is not critical", {"power": "low"}),
    ("we must have reliable electricity", {"power": "critical"}),
    ("I don't need cheap land", {"land": "low"}),
    ("electronics plant near an airport", {}),
    ("we need support for our dairy", {}),
    ("important: chemicals plant", {}),
])
def test_modifier_parse(text, expected):
    assert modifiers.parse(text).as_map() == expected


def test_unparsed_field_without_cue():
    p = modifiers.parse("scarce water region, food plant")
    assert p.as_map() == {} and p.unparsed_fields == ["water"]
    assert modifiers.parse("water", default_label="high").as_map() == {"water": "high"}


def test_apply_multipliers_and_floor():
    keys = scoring.dataset().keys
    base = {k: 0.0 for k in keys} | {"power": 0.5, "water": 0.5}
    w = modifiers.apply(base, {"water": "critical"})
    assert w["water"] == pytest.approx(1.5 / 2.0) and w["power"] == pytest.approx(0.5 / 2.0)
    w = modifiers.apply(base, {"rail": "high"})  # zero base gets the 0.05 floor first
    assert w["rail"] == pytest.approx(0.09 / 1.09)
    w = modifiers.apply(base, {"water": "exclude"})
    assert w["water"] == 0 and w["power"] == pytest.approx(1.0)


def test_apply_override_is_absolute():
    keys = scoring.dataset().keys
    base = {k: 0.0 for k in keys} | {"power": 0.5, "water": 0.3, "rail": 0.2}
    w = modifiers.apply(base, {"water": "high"}, {"power": 0.4})
    assert w["power"] == pytest.approx(0.4) and sum(w.values()) == pytest.approx(1.0)
    assert w["water"] / w["rail"] == pytest.approx(0.3 * 1.8 / 0.2)


def test_apply_override_splits_evenly_when_others_are_zero():
    keys = scoring.dataset().keys
    base = {k: 0.0 for k in keys} | {"power": 1.0}
    w = modifiers.apply(base, {}, {"power": 0.6})
    others = [w[k] for k in keys if k != "power"]
    assert w["power"] == pytest.approx(0.6) and sum(w.values()) == pytest.approx(1.0)
    assert all(v == pytest.approx(0.4 / len(others)) for v in others)


# ---------- industry resolver (§7.3) ----------

@pytest.mark.parametrize("text,pid,tier", [
    ("we make carpets", "preset:textile", "lexicon"),
    ("cardboard box factory", "preset:general_manufacturing", "lexicon"),
    ("I want to set up a factory", "preset:general_manufacturing", "generic"),
    ("autmobile plant", "preset:automobile", "typo"),
    ("tekstile mill", "preset:textile", "typo"),
    ("electronis factory", "preset:electronics", "typo"),
    ("sugr mill", "preset:sugar_ethanol", "typo"),
    ("diary plant", "preset:dairy", "typo"),
    ("potato chips factory", "preset:food_beverages", "lexicon"),
    ("mineral water bottling", "preset:food_beverages", "lexicon"),
])
def test_resolver_rule_tiers(text, pid, tier):
    r = resolve(text, allow_embedding=False)
    assert (r.status, r.profile_id, r.tier) == ("match", pid, tier)


def test_resolver_ambiguous_and_unsupported():
    r = resolve("cotton ginning", allow_embedding=False)
    assert r.status == "ambiguous" and set(r.candidates) == {"preset:textile", "preset:agro_cold"}
    r = resolve("cement plant", allow_embedding=False)
    assert r.status == "unsupported" and r.resource == "limestone deposits"
    assert "preset:general_manufacturing" in intent.unsupported_chips("cement")


def test_no_silent_general_default():
    r = resolve("space laser factory", allow_embedding=False)
    assert r.status == "none" and r.has_signal  # never general manufacturing


def test_typo_tier_leaves_real_words_alone():
    for text in ("caring for cardiac patients", "space travel", "frozen peas packing"):
        assert resolve(text, allow_embedding=False).tier != "typo"


# ---------- router + sessions ----------

def test_session_lifecycle(client):
    r = client.post("/api/session").json()
    assert r["state"]["profile"] is None and r["state"]["ranking"] == []
    assert client.get(f"/api/session/{r['session_id']}").status_code == 200
    assert client.get("/api/session/nope").status_code == 404
    assert client.post("/api/message", json={"session_id": "nope", "text": "hi"}).status_code == 404
    assert client.post("/api/message", json={"session_id": r["session_id"], "text": ""}).status_code == 422


def chip(b: dict, label: str) -> str:
    return next(c["value"] for c in b["chips"] if c["label"] == label)


def test_suggest_then_apply_returns_full_state(client):
    sid = session(client)
    b = say(client, sid, "I want to set up a food processing plant where water is critical")
    assert b["kind"] == "suggest_preset" and b["state"]["profile"] is None and b["state"]["ranking"] == []
    assert b["state"]["pending_suggest"] == {"preset_id": "preset:food_beverages", "modifiers": {"water": "critical"}}
    assert "This sounds like **Food & Beverages**" in b["reply_md"] and "water supply matter most" in b["reply_md"]
    assert [c["label"] for c in b["chips"]] == ["Apply Food & Beverages", "Pick another"]
    b = say(client, sid, chip(b, "Apply Food & Beverages"))
    assert b["kind"] == "apply_preset" and b["state"]["profile"]["id"] == "preset:food_beverages"
    assert b["state"]["modifiers"] == {"water": "critical"} and b["state"]["pending_suggest"] is None
    assert len(b["state"]["ranking"]) == 36 and b["turn_id"]
    assert "Food & Beverages" in b["reply_md"] and "excluded (no industrial land)" in b["reply_md"]
    assert "%" not in b["reply_md"] and "**Water supply** now matters most" in b["reply_md"]


def test_dont_care_about_water_is_adjust_not_auto(client):
    sid = session(client, "preset:food_beverages")
    b = say(client, sid, "don't care about water")
    assert b["kind"] == "adjust" and b["state"]["profile"]["id"] == "preset:food_beverages"
    assert b["state"]["modifiers"] == {"water": "low"}


def test_other_industry_is_only_suggested(client):
    sid = session(client, "preset:food_beverages")
    say(client, sid, "water is critical")
    before = client.get(f"/api/session/{sid}").json()["state"]
    b = say(client, sid, "I need an automobile plant with good power")
    assert b["kind"] == "suggest_preset" and b["state"]["profile"]["id"] == "preset:food_beverages"
    assert b["state"]["ranking"] == before["ranking"] and b["state"]["modifiers"] == {"water": "critical"}
    assert b["state"]["pending_suggest"] == {"preset_id": "preset:automobile", "modifiers": {"power": "high"}}
    b = say(client, sid, chip(b, "Apply Automobile"))
    assert b["kind"] == "apply_preset" and b["state"]["profile"]["id"] == "preset:automobile"
    assert b["state"]["modifiers"] == {"power": "high"}  # applying cleared water, then added the pending power


def test_yes_applies_the_pending_suggestion(client):
    sid = session(client)
    say(client, sid, "cheese factory")
    b = say(client, sid, "yes")
    assert b["kind"] == "apply_preset" and b["state"]["profile"]["id"] == "preset:dairy"


def test_pick_another_changes_nothing(client):
    sid = session(client, "preset:pharma")
    b = say(client, sid, "tyre factory")
    b = say(client, sid, chip(b, "Pick another"))
    assert b["kind"] == "pick_preset" and b["state"]["profile"]["id"] == "preset:pharma"
    assert len(b["chips"]) == 12 and b["chips"][0] == {"label": "Automobile", "value": "Apply Automobile"}


def test_menu_selection_applies_pending_modifiers(client):
    sid = session(client)
    say(client, sid, "tyre factory where power is critical")  # suggests Plastics & Rubber
    st = client.post(f"/api/session/{sid}/profile", json={"profile_id": "preset:plastics_rubber"}).json()["state"]
    assert st["profile"]["id"] == "preset:plastics_rubber" and st["modifiers"] == {"power": "critical"}
    assert st["pending_suggest"] is None


def test_menu_selection_of_another_profile_starts_clean(client):
    sid = session(client)
    say(client, sid, "Food processing plant, water is critical")
    st = client.post(f"/api/session/{sid}/profile", json={"profile_id": "preset:dairy"}).json()["state"]
    assert st["profile"]["id"] == "preset:dairy" and st["modifiers"] == {} and st["slider_overrides"] == {}
    assert st["pending_suggest"] is None


def test_same_industry_is_not_a_suggestion(client):
    sid = session(client, "preset:dairy")
    b = say(client, sid, "cheese factory")
    assert b["kind"] == "current_preset" and b["state"]["pending_suggest"] is None
    b = say(client, sid, "cheese factory where water is critical")
    assert b["kind"] == "adjust" and b["state"]["modifiers"] == {"water": "critical"}


def test_compare_never_reranks(client):
    sid = session(client, "preset:automobile")
    before = client.get(f"/api/session/{sid}").json()["state"]
    b = say(client, sid, "compare Pune and Nashik")
    assert b["kind"] == "compare"
    assert b["state"]["ranking"] == before["ranking"] and b["state"]["weights"] == before["weights"]
    rows = b["facts"]["compare"]["rows"]
    assert len(rows) == 13 and all(r["leader"] in ("a", "b", "tie") for r in rows)


def test_explain_and_pronoun(client):
    sid = session(client, "preset:dairy")
    b = say(client, sid, "tell me about Kolhapur")
    assert b["kind"] == "explain_district" and b["state"]["focus_district"] == 17
    b = say(client, sid, "and its weaknesses?")
    assert b["kind"] == "explain_district" and b["understood"]["districts"] == [17]


def test_modifier_only_without_profile_offers_general(client):
    sid = session(client)
    b = say(client, sid, "power is critical")
    assert b["kind"] == "suggest_preset" and b["state"]["profile"] is None  # never applied silently
    assert b["state"]["pending_suggest"] == {"preset_id": "preset:general_manufacturing", "modifiers": {"power": "critical"}}
    assert [c["label"] for c in b["chips"]] == ["Use General Manufacturing", "Pick an industry"]
    b = say(client, sid, chip(b, "Use General Manufacturing"))
    assert b["state"]["profile"]["id"] == "preset:general_manufacturing" and b["state"]["modifiers"] == {"power": "critical"}


def test_save_from_chat_with_and_without_a_name(client):
    sid = session(client, "preset:agro_cold")
    say(client, sid, "don't care about water")
    b = say(client, sid, "save this as Test agro plan")
    pid = b["state"]["profile"]["id"]
    try:
        assert b["kind"] == "save_profile" and pid.startswith("user:") and "Saved as **Test agro plan**" in b["reply_md"]
        saved = next(p for p in client.get("/api/profiles").json()["user"] if p["id"] == pid)
        assert saved["base_profile_id"] == "preset:agro_cold" and saved["weights"] == b["state"]["weights"]
        b = say(client, sid, "reset")
        assert b["state"]["profile"]["id"] == pid and b["state"]["weights"] == saved["weights"]
    finally:
        client.delete(f"/api/profiles/{pid}")

    sid = session(client, "preset:pharma")
    b = say(client, sid, "keep this profile")
    assert b["kind"] == "save_profile" and b["reply_md"] == "What should I call it?"
    assert b["state"]["pending_save"] and b["chips"][0]["label"] == "Pharmaceuticals (custom)"
    b = say(client, sid, "cancel")
    assert b["kind"] == "save_cancel" and b["state"]["pending_save"] is None
    assert b["state"]["profile"]["id"] == "preset:pharma"
    b = say(client, sid, "Save as my profile")
    b = say(client, sid, chip(b, "Pharmaceuticals (custom)"))
    pid = b["state"]["profile"]["id"]
    try:
        assert b["kind"] == "save_profile" and pid.startswith("user:") and b["state"]["pending_save"] is None
    finally:
        client.delete(f"/api/profiles/{pid}")


def test_save_needs_a_profile(client):
    sid = session(client)
    b = say(client, sid, "save this")
    assert b["kind"] == "save_profile" and b["state"]["profile"] is None and b["state"]["pending_save"] is None


def test_save_profile_endpoint(client):
    sid = session(client)
    assert client.post(f"/api/session/{sid}/save_profile", json={"name": "x"}).status_code == 409
    client.post(f"/api/session/{sid}/profile", json={"profile_id": "preset:logistics"})
    r = client.post(f"/api/session/{sid}/save_profile", json={"name": "Endpoint save"}).json()
    try:
        assert r["profile"]["name"] == "Endpoint save" and r["state"]["profile"]["id"] == r["profile"]["id"]
        assert client.post(f"/api/session/{sid}/save_profile", json={"name": ""}).status_code == 422
    finally:
        client.delete(f"/api/profiles/{r['profile']['id']}")


def test_adjust_offers_save_chip_and_uses_words(client):
    sid = session(client, "preset:chemicals")
    b = say(client, sid, "land cost doesn't matter")
    assert b["kind"] == "adjust" and {"label": "Save as my profile", "value": "Save as my profile"} in b["chips"]
    assert "**Cheap land** now matters less" in b["reply_md"] and "%" not in b["reply_md"]


def test_unsupported_clarify_then_chip(client):
    sid = session(client)
    b = say(client, sid, "steel plant")
    assert b["kind"] == "clarify" and b["state"]["pending_clarify"]
    assert "I don't have the data to rank steel reliably" in b["reply_md"]
    labels = [c["label"] for c in b["chips"]]
    assert "General Manufacturing" in labels
    b = say(client, sid, "General Manufacturing")
    assert b["kind"] == "clarify_answer" and b["state"]["profile"]["id"] == "preset:general_manufacturing"
    assert b["state"]["pending_clarify"] is None


def test_ambiguous_clarify_by_number(client):
    sid = session(client)
    b = say(client, sid, "cotton ginning")
    assert b["kind"] == "clarify"
    first = b["state"]["pending_clarify"]["candidates"][0]
    b = say(client, sid, "1")
    assert b["state"]["profile"]["id"] == first


def test_help_and_chat(client):
    sid = session(client)
    assert say(client, sid, "hello")["kind"] == "help"
    b = say(client, sid, "hi, I want a textile mill")
    assert b["kind"] == "suggest_preset"
    say(client, sid, chip(b, "Apply Textile & Apparel"))
    b = say(client, sid, "show me all 36")
    assert b["kind"] == "chat" and b["chips"][0]["value"] == "action:show_all"


def test_reset_restores_base(client):
    sid = session(client, "preset:textile")
    say(client, sid, "ignore crop output")
    b = say(client, sid, "reset weights")
    st = b["state"]
    assert b["kind"] == "reset" and st["modifiers"] == {} and st["weights"] == st["base_weights"]


def test_offline_router_still_works(client, offline):
    sid = session(client)
    assert say(client, sid, "cheese factory")["state"]["pending_suggest"]["preset_id"] == "preset:dairy"
    b = say(client, sid, "space laser factory")
    assert b["kind"] == "clarify" and len(b["chips"]) == 13  # all 12 presets + Pick an industry


# ---------- slider and profile endpoints ----------

def test_weights_endpoint(client):
    sid = session(client)
    assert client.post(f"/api/session/{sid}/weights", json={"overrides": {"power": 0.3}}).status_code == 409
    client.post(f"/api/session/{sid}/profile", json={"profile_id": "preset:electronics"})
    for bad in ({"power": -0.1}, {"power": 1.5}, {"bogus": 0.2}, {"power": None}):
        assert client.post(f"/api/session/{sid}/weights", json={"overrides": bad}).status_code == 422
    st = client.post(f"/api/session/{sid}/weights", json={"overrides": {"literacy": 0.4}}).json()["state"]
    assert st["weights"]["literacy"] == pytest.approx(0.4)
    st = client.post(f"/api/session/{sid}/weights", json={"constraints": {"requires_industrial_land": False}}).json()["state"]
    assert all(e["eligible"] for e in st["ranking"])
    st = client.post(f"/api/session/{sid}/reset").json()["state"]
    assert st["slider_overrides"] == {} and st["constraints"]["requires_industrial_land"] is True


def test_district_uses_session_weights(client):
    sid = session(client, "preset:food_beverages")
    say(client, sid, "water is critical")
    body = client.get("/api/district/8", params={"session_id": sid}).json()
    st = client.get(f"/api/session/{sid}").json()["state"]
    assert body["weights"] == st["weights"]
    assert body["entry"] == next(e for e in st["ranking"] if e["district_id"] == 8)


def test_profile_crud(client):
    body = {"name": "Test profile", "weights": {"power": 2, "water": 1, "land": 1}, "rationale": "test"}
    p = client.post("/api/profiles", json=body).json()["profile"]
    assert p["id"].startswith("user:") and p["weights"]["power"] == pytest.approx(0.5)
    assert any(x["id"] == p["id"] for x in client.get("/api/profiles").json()["user"])
    p2 = client.put(f"/api/profiles/{p['id']}", json={"name": "Renamed"}).json()["profile"]
    assert p2["name"] == "Renamed" and p2["weights"] == p["weights"]
    assert client.put("/api/profiles/preset:textile", json={"name": "x"}).status_code == 403
    assert client.delete("/api/profiles/preset:textile").status_code == 403
    assert client.post("/api/profiles", json={**body, "weights": {"power": -1}}).status_code == 422
    assert client.delete(f"/api/profiles/{p['id']}").status_code == 204
    assert client.delete(f"/api/profiles/{p['id']}").status_code == 404


def test_unreadable_negation_asks_instead_of_guessing(client):
    sid = session(client, "preset:automobile")
    b = say(client, sid, "port distance is not something we lose sleep over")
    assert b["kind"] == "clarify_modifier" and b["state"]["modifiers"] == {}
    assert {c["value"] for c in b["chips"]} == {"port is important", "port is not important"}


@pytest.mark.parametrize("text,expected", [
    ("port distance doesn't bother us", {"port": "low"}),
    ("we can't compromise on power supply", {"power": "critical"}),
    ("slightly less focus on ports", {"port": "low"}),
    ("port matters most to us", {"port": "critical"}),
    ("port is critical and water is not", {"port": "critical", "water": "low"}),
    ("we don't want expensive land", {"land": "high"}),
    ("we don't ship by sea", {"port": "low"}),
])
def test_modifier_direction(text, expected):
    assert modifiers.parse(text).as_map() == expected


def test_for_phrase_names_the_customer():
    assert resolve("cartons for pharma companies", allow_embedding=False).profile_id == "preset:general_manufacturing"


def test_reset_after_active_user_profile_deleted(client):
    sid = client.post("/api/session").json()["session_id"]
    client.post(f"/api/session/{sid}/profile", json={"profile_id": "preset:dairy"})
    client.post(f"/api/session/{sid}/weights", json={"overrides": {"water": 0.5}})
    st = client.post(f"/api/session/{sid}/save_profile", json={"name": "temp reset check"}).json()["state"]
    pid = st["profile"]["id"]
    client.post(f"/api/session/{sid}/weights", json={"overrides": {"port": 0.4}})
    assert client.delete(f"/api/profiles/{pid}").status_code == 204
    r = client.post(f"/api/session/{sid}/reset")
    assert r.status_code == 200
    st = r.json()["state"]
    assert st["slider_overrides"] == {} and st["weights"] == pytest.approx(st["base_weights"])
