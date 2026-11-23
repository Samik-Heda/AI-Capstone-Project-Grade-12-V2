"""P9b: the advisor (§18.2) — follow-up chips, trade-off tip, proactive questions, previewed answers.
All deterministic; no Ollama."""
import json
from pathlib import Path

import pytest

from backend import advisor, router, templates
from backend.session import Session

ROOT = Path(__file__).resolve().parent.parent


def fresh(profile: str | None = None) -> Session:
    s = Session(id="t")
    if profile:
        s.set_profile(profile)
    return s


def say(s: Session, text: str) -> dict:
    return router.handle(s, text)


def load(name: str) -> list[dict]:
    return [json.loads(x) for x in open(ROOT / "eval" / name, encoding="utf-8")]


# ---------- answers ----------

@pytest.mark.parametrize("row", load("advisor_answers.jsonl") + load("advisor_answers_holdout.jsonl"),
                         ids=lambda r: r["text"])
def test_answer_lexicon(row):
    got = advisor.classify_answer(row["text"])
    up = {"high", "critical"}
    assert (got in up) == (row["expect"] in up) and (got in up or got == row["expect"])


@pytest.mark.parametrize("text", ["compare Pune and Nashik", "Do you have data on Satara?", "what about a dairy plant",
                                  "show me the top districts for textiles"])
def test_non_answers_are_not_answers(text):
    assert advisor.classify_answer(text) is None


def test_long_text_is_not_a_short_answer():
    assert advisor.classify_answer("yes " + "and also we will think about many other things " * 3) is None


# ---------- questions ----------

def test_question_follows_apply_and_changes_top3():
    s = fresh()
    say(s, "food processing plant")
    r = say(s, "Apply Food & Beverages")
    q = r["question"]
    assert q and q["field"] in advisor.QUESTIONS and q["text"] == advisor.QUESTIONS[q["field"]]["q"]
    base = advisor._top_ids(s.weights, s.constraints)
    impact = max(advisor._impact(base, advisor._what_if(s, q["field"], lab)) for lab in ("high", "low"))
    assert impact > 0
    assert s.asked_fields == [q["field"]]


def test_answer_is_a_preview_until_applied():
    s = fresh()
    say(s, "food processing plant")
    say(s, "Apply Food & Beverages")
    f = s.pending_question["field"]
    before = dict(s.weights)
    r = say(s, "yes, a lot")
    assert r["kind"] == "advisor_answer" and s.weights == before  # nothing changed yet
    assert s.pending_change == {"modifiers": {f: "critical"}}
    assert [c["value"] for c in r["chips"][:2]] == ["Apply this change", "Keep as is"]
    assert s.business_notes[f] == advisor.QUESTIONS[f]["yes"]
    assert "would" in r["reply_md"]
    r = say(s, "Apply this change")
    assert r["kind"] == "adjust" and s.modifiers == {f: "critical"} and s.weights != before


def test_keep_as_is_changes_nothing():
    s = fresh()
    say(s, "Apply Food & Beverages")
    before = dict(s.weights)
    say(s, "not really")
    r = say(s, "Keep as is")
    assert r["kind"] == "decline_change" and s.weights == before and s.pending_change is None


def test_unsure_answer_changes_nothing():
    s = fresh()
    say(s, "Apply Dairy")
    assert s.pending_question
    before = dict(s.weights)
    r = say(s, "not sure")
    assert r["kind"] == "advisor_answer" and s.pending_change is None and s.weights == before


def test_question_dropped_when_user_moves_on():
    s = fresh()
    say(s, "Apply Dairy")
    assert s.pending_question
    r = say(s, "compare Pune and Nashik")
    assert r["kind"] == "compare" and s.pending_question is None
    r = say(s, "yes")  # no open question any more: not read as an answer
    assert r["kind"] != "advisor_answer"


def test_yes_applies_pending_change():
    s = fresh()
    say(s, "Apply Dairy")
    say(s, "a lot")
    r = say(s, "yes")
    assert r["kind"] == "adjust" and s.modifiers


def test_never_repeats_and_at_most_three_per_session():
    s = fresh()
    say(s, "Apply Automobile")
    asked = []
    for i in range(30):
        if s.pending_question:
            asked.append(s.pending_question["field"])
            say(s, "yes" if i % 2 else "not really")
            say(s, "Apply this change")
        else:
            say(s, "land is not important" if i % 2 else "power is critical")
    assert len(asked) == len(set(asked)) <= 3
    assert s.asked_fields == asked or len(s.asked_fields) <= 3


def test_min_gap_between_questions():
    s = fresh()
    say(s, "Apply Automobile")
    first = s.last_question_turn
    assert first is not None
    say(s, "yes")  # answer (turn 2): never asks right after
    assert s.pending_question is None
    r = say(s, "Apply this change")  # turn 3 — gap 2 reached
    if r["question"]:
        assert s.turn_no - first >= 2


def test_no_question_when_answers_cannot_matter(monkeypatch):
    s = fresh()
    monkeypatch.setattr(advisor, "_impact", lambda a, b: 0)
    r = say(s, "Apply Dairy")
    assert r["question"] is None and s.asked_fields == []


def test_no_question_for_set_fields():
    s = fresh()
    say(s, "Apply Dairy")
    s.asked_fields.clear()
    s.last_question_turn = None
    s.apply_modifiers({k: "high" for k in advisor.QUESTIONS})
    assert advisor.pick_question(s, "adjust") is None


# ---------- tip ----------

def _expected_tip(s):
    elig = [e for e in s.ranking if e["eligible"]]
    ids = [e["district_id"] for e in sorted(elig, key=lambda e: e["rank"])]
    cut = max(1, round(len(ids) * 25 / 36))
    for key in [k for k, w in sorted(s.weights.items(), key=lambda kv: -kv[1]) if w > 0][:4]:
        r, _ = templates._field_rank(ids[0], key, ids)
        if r >= cut:
            return key
    return None


def test_tip_matches_rule_for_every_preset():
    from backend import db
    for p in [p["id"] for p in db.profiles() if p["kind"] == "preset"]:
        s = fresh(p)
        t = advisor.tip(s)
        assert (t["field"] if t else None) == _expected_tip(s), p


def test_tip_shown_in_reply_and_facts(monkeypatch):
    s = fresh()
    monkeypatch.setattr(advisor, "tip", lambda s: {"district_id": 1, "name": "Pune", "field": "water", "rank": 30, "of": 34})
    r = say(s, "Apply Dairy")
    assert r["facts"]["tip"]["field"] == "water" and "Heads up" in r["reply_md"] and "30th of 34" in r["reply_md"]


# ---------- follow-up chips ----------

FOLLOW_KIND = {"why is": "explain_district", "compare": "compare", "tell me about": "explain_district"}


def _kind_for(value: str) -> str:
    v = value.lower()
    return next(k for p, k in FOLLOW_KIND.items() if v.startswith(p))


@pytest.mark.parametrize("setup", [["Apply Automobile"], ["Apply Dairy", "tell me about Satara"],
                                   ["Apply Textile & Apparel", "compare Pune and Solapur"],
                                   ["Apply Pharmaceuticals", "water is critical"]])
def test_followup_chips_route_as_intended(setup):
    s = fresh()
    for t in setup:
        r = say(s, t)
    follow = [c for c in r["chips"] if c["value"] not in ("Save as my profile", "make a business brief")]
    assert follow and len(r["chips"]) <= advisor.MAX_CHIPS
    for c in follow:
        trial = fresh()
        for t in setup:
            say(trial, t)
        trial.pending_question = None  # the chip is clicked instead of answering
        assert say(trial, c["value"])["kind"] == _kind_for(c["value"]), c


def test_merge_chips_keeps_existing_first_and_caps():
    base = [{"label": "A", "value": "a"}]
    extra = [{"label": x, "value": x} for x in "abcdef"]
    out = advisor.merge_chips(base, extra)
    assert out[0]["label"] == "A" and len(out) == advisor.MAX_CHIPS and len({c["value"] for c in out}) == len(out)
