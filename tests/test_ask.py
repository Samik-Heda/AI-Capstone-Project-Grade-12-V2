"""P10: questions get answers, what-ifs get previews, curated industry needs, the brief checklist and the
stale-code flag (§19). No Ollama needed."""
import json
from pathlib import Path

import pytest

from backend import db, narrate, router
from backend.session import Session

ROOT = Path(__file__).resolve().parent.parent
SUITE = [json.loads(x) for x in open(ROOT / "eval" / "ask_suite.jsonl", encoding="utf-8")]


def sess(profile="preset:electronics") -> Session:
    s = Session(id="a")
    if profile:
        s.set_profile(profile)
    return s


@pytest.mark.parametrize("row", SUITE, ids=lambda r: r["text"][:40])
def test_suite_kind_and_no_silent_change(row):
    s = sess(row["profile"])
    before = dict(s.weights)
    r = router.handle(s, row["text"])
    kind = "ask" if r["kind"] == "chat" and row["expect"] == "ask" else r["kind"]
    assert kind == row["expect"]
    if row["expect"] != "adjust":
        assert s.weights == before  # questions and what-ifs never change weights


def test_user_transcript_replays():
    """The 2026-10-04 transcript: semiconductors, lots of workers, questions."""
    s = sess(None)
    r = router.handle(s, "i wanna make a business for manufacturin semi conductors")
    assert r["kind"] == "suggest_preset" and r["state"]["pending_suggest"]["preset_id"] == "preset:electronics"
    router.handle(s, "Apply Electronics")
    r = router.handle(s, "i want lots of workers and water")
    assert r["kind"] == "adjust" and set(r["facts"]["changes"]) == {"literacy", "water"}
    for q in ("any other factors i should be concerned about?", "what all might be required?"):
        r = router.handle(s, q)
        assert r["kind"] == "ask" and "Also check (not in our data)" in r["reply_md"]
        assert "ultra-pure water" in r["reply_md"].lower() and r["chips"][-1]["value"] == "action:more"
    w = dict(s.weights)
    r = router.handle(sess(), "how much water does a chip fab need")
    assert r["kind"] == "ask" and any(c["label"] == "Make water supply matter more" for c in r["chips"])
    assert s.weights == w


def test_question_naming_other_industry_answers_and_offers_apply():
    s = sess("preset:food_beverages")
    r = router.handle(s, "how much water does a chip fab need")
    assert r["kind"] == "ask" and r["chips"][0]["value"] == "Apply Electronics"
    assert s.profile_id == "preset:food_beverages" and s.pending_suggest["preset_id"] == "preset:electronics"


def test_ask_is_narrated_with_advisor_prompt():
    s = sess()
    s.business_notes["water"] = "Uses a lot of water"
    r = router.handle(s, "what all might be required?", narration_ready=True)
    assert r["narration"]["available"]
    sheet = narrate.build_sheet(s.find_turn(r["turn_id"]).ctx)
    assert sheet.system == narrate.ADVISOR_PROMPT and sheet.task == "ask" and sheet.num_predict == 170
    assert "Not in the data, worth checking" in sheet.text and "The owner said: uses a lot of water" in sheet.text
    assert "what all might be required?" in sheet.text
    detail = narrate.build_sheet(s.find_turn(r["turn_id"]).ctx, detail=True)
    assert detail.num_predict == 260 and "in detail" in detail.text


def test_general_advice_numbers_still_checked():
    s = sess()
    r = router.handle(s, "what all might be required?", narration_ready=True)
    sheet = narrate.build_sheet(s.find_turn(r["turn_id"]).ctx)
    g = narrate.check("You need ultra-pure water.\nGeneral advice:\n- Budget 12345 litres a day.", sheet)
    assert not g["grounded"] and "12345" in g["unsupported_numbers"]


# ---------- what-if ----------

def test_what_if_previews_without_change():
    s = sess("preset:electronics")  # port weight 0.05 (dairy ignores the port)
    before = dict(s.weights)
    r = router.handle(s, "what if I didn't need a port")
    assert r["kind"] == "what_if" and s.weights == before
    assert s.pending_change and r["chips"][0]["value"] == "Apply this change" and "Nothing has changed yet" in r["reply_md"]
    p = r["facts"]["preview"]
    from backend import advisor
    assert p == advisor.preview(s, r["facts"]["changes"])  # the preview is a non-committing re-rank
    r = router.handle(s, "Apply this change")
    assert r["kind"] == "adjust" and s.weights != before


@pytest.mark.parametrize("text", ["what if I can't find workers?", "what if I export everything?"])
def test_scenarios_without_priority_cue_are_questions(text):
    assert router.handle(sess(), text)["kind"] == "ask"


def test_advisor_never_asks_about_ignored_factors():
    from backend import advisor
    s = sess()
    q = advisor.pick_question(s, "apply_preset")
    assert q is None or s.base_weights[q["field"]] > 0
    for _ in range(3):
        q = advisor.pick_question(s, "adjust")
        if not q:
            break
        assert s.base_weights[q["field"]] > 0
        s.asked_fields.append(q["field"])


# ---------- curated needs and the brief ----------

def test_industry_needs_cover_every_preset():
    presets = [p["id"] for p in db.profiles() if p["kind"] == "preset"]
    allp = db.industry_needs_all()
    assert set(allp) == set(presets)
    for pid in presets:
        assert 4 <= len(allp[pid]) <= 6
        assert all(n["need"] and n["why"] and n["step"] for n in allp[pid])


def test_industry_needs_for_user_profile_uses_base():
    from backend import profiles
    p = profiles.create("Needs test", {"water": 1.0}, None, "x", base_profile_id="preset:dairy")
    try:
        assert db.industry_needs(p["id"]) == db.industry_needs("preset:dairy")
    finally:
        profiles.delete(p["id"])
    assert db.industry_needs(None) == []


def test_brief_has_also_check():
    from backend import brief
    b = brief.build(sess("preset:pharma"))
    assert b["also_check"] == db.industry_needs("preset:pharma")


# ---------- stale-code flag ----------

def test_health_reports_code_stale(client, monkeypatch):
    from backend import main
    assert client.get("/api/health").json()["code_stale"] is False
    monkeypatch.setattr(main, "code_fingerprint", lambda: "changed")
    assert client.get("/api/health").json()["code_stale"] is True
