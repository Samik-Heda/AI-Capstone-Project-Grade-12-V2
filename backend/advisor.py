"""Advisor (spec §18.2): follow-up chips, a trade-off tip and one proactive question at a time.

Everything here is deterministic and built from the ranking; no LLM. The question is asked only when
its answer would change the top 3, and an answer is always a preview with an Apply chip, never a change."""
from __future__ import annotations

import re
from functools import lru_cache

import numpy as np

from . import config, nlp, scoring, templates
from . import modifiers as mod

# One plain question per factor, with the note kept for the business brief for each kind of answer.
QUESTIONS: dict[str, dict[str, str]] = {
    "literacy": {"q": "Will you need many trained or educated workers, like technicians, operators or office staff?",
                 "yes": "Needs many trained workers", "no": "Does not need many trained workers"},
    "road_density": {"q": "Will your trucks or vans go to many nearby towns and villages on local roads every day?",
                     "yes": "Delivers locally every day", "no": "Few local deliveries"},
    "highway": {"q": "Will you send goods by truck over long distances, so being near a national highway matters?",
                "yes": "Ships long distances by road", "no": "Little long-distance trucking"},
    "rail": {"q": "Will you move heavy goods in bulk, where a railway line nearby would help?",
             "yes": "Moves heavy goods in bulk", "no": "No bulk rail freight"},
    "port": {"q": "Will you export or import goods by sea?",
             "yes": "Exports or imports by sea", "no": "Sells mainly inside India"},
    "power": {"q": "Will your plant run heavy machines that need steady electricity all day?",
              "yes": "Runs power-hungry machines", "no": "Uses little electricity"},
    "water": {"q": "Will your plant use a lot of water, for cooling, washing or processing?",
              "yes": "Uses a lot of water", "no": "Uses little water"},
    "crop_output": {"q": "Will you buy large amounts of crops or farm produce as raw material?",
                    "yes": "Buys a lot of farm produce", "no": "Does not depend on farm produce"},
    "crop_diversity": {"q": "Do you need many different crops through the year, not just one?",
                       "yes": "Needs many different crops", "no": "Works with one or two crops"},
    "irrigation": {"q": "Do your raw materials come from irrigated farms, like sugarcane, fruit or vegetables?",
                   "yes": "Relies on irrigated farms", "no": "Does not rely on irrigated farms"},
    "estates": {"q": "Would you like to set up inside a ready industrial area (MIDC) with roads and power in place?",
                "yes": "Wants a plot in an industrial area", "no": "Does not need an industrial area"},
    "msme": {"q": "Will you rely on small local suppliers or workshops for parts and services?",
             "yes": "Relies on small local suppliers", "no": "Few local suppliers needed"},
    "land": {"q": "Do you need a big plot of land, so a low land price matters a lot?",
             "yes": "Needs a big, low-cost plot", "no": "Land price is not a big concern"},
}

RANK_KINDS = ("apply_preset", "clarify_answer", "adjust", "current_preset", "reset", "apply_change")
ASK_AFTER = ("apply_preset", "clarify_answer", "adjust", "apply_change")
MAX_CHIPS = 4

# Short answers to a question. Checked in this order: unsure, negative, strong, positive.
UNSURE = {"not sure", "dont know", "don t know", "do not know", "no idea", "maybe", "skip", "idk", "not certain",
          "pass", "hard to say", "depends", "it depends", "unsure", "perhaps", "next question", "rather not say",
          "not yet", "no clue", "undecided", "not decided", "yet to decide", "cant say", "can t say", "who knows"}
NEGATIVE = {"no", "nope", "nah", "nahi", "not", "never", "hardly", "barely", "rarely", "little", "few", "none", "dont",
            "don t", "do not", "does not", "doesnt", "won t", "wont", "will not", "isn t", "isnt", "minimal",
            "negligible", "zero", "nothing", "not really", "not much", "not at all", "very little", "small amount"}
STRONG = {"a lot", "lots", "loads", "huge", "very", "heavily", "massive", "plenty", "tons", "crucial", "critical",
          "essential", "must", "extremely", "really important", "very important", "all day", "constantly",
          "big time", "absolutely", "enormous", "large amounts", "a great deal"}
POSITIVE = {"yes", "yeah", "yep", "yup", "sure", "of course", "definitely", "certainly", "correct", "right",
            "we do", "we will", "it does", "it will", "i do", "i will", "true", "ok", "okay", "some", "probably",
            "likely", "somewhat", "moderately", "a bit", "partly", "regularly", "often", "we need", "it matters",
            "it is important", "its important", "yes please", "we would", "i would", "would like", "we export",
            "we import", "they do", "it is", "we are", "i am", "kind of", "sort of", "sometimes", "occasionally",
            "at times", "now and then", "haan", "ha", "ji"}
ANSWER_MAX_TOKENS = 12


def cfg() -> dict:
    return config.load().get("advisor", {})


def _eligible(ranking: list[dict]) -> list[dict]:
    return sorted((e for e in ranking if e["eligible"]), key=lambda e: e["rank"])


def ordinal(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


# ---------- follow-up chips ----------

def followups(kind: str, s, fact: dict | None) -> list[dict]:
    """2–3 next steps for this reply. Values are plain sentences the router already understands."""
    elig = _eligible(s.ranking)
    if not s.profile_id or not elig:
        return []
    t1 = elig[0]["name"]
    t2 = elig[1]["name"] if len(elig) > 1 else None
    out: list[dict] = []
    if kind in RANK_KINDS:
        out.append({"label": f"Why is {t1} first?", "value": f"why is {t1} ranked first?"})
        if t2:
            out.append({"label": f"Compare {t1} and {t2}", "value": f"compare {t1} and {t2}"})
        if BRIEF_ENABLED:
            out.append(BRIEF_CHIP)
    elif kind == "explain_district" and fact and fact.get("entry"):
        e = fact["entry"]
        if e["eligible"] and e["rank"] != 1:
            out.append({"label": f"Compare with {t1}", "value": f"compare {e['name']} and {t1}"})
            out.append({"label": f"Why isn't {e['name']} first?", "value": f"why is {e['name']} not first?"})
        elif e["eligible"] and t2:
            out.append({"label": f"Compare with {t2}", "value": f"compare {e['name']} and {t2}"})
    elif kind == "compare" and fact and fact.get("compare"):
        cmp = fact["compare"]
        win = cmp["a"] if cmp["direction"] != "b" else cmp["b"]
        lose = cmp["b"] if win is cmp["a"] else cmp["a"]
        out.append({"label": f"Tell me about {win['name']}", "value": f"tell me about {win['name']}"})
        if lose["eligible"]:
            out.append({"label": f"Why is {lose['name']} behind?", "value": f"why is {lose['name']} not first?"})
    return out


BRIEF_CHIP = {"label": "Make a business brief", "value": "make a business brief"}
BRIEF_ENABLED = True  # §18.3


def merge_chips(chips: list[dict], extra: list[dict]) -> list[dict]:
    """Existing chips first (they answer this turn), then follow-ups, at most MAX_CHIPS, no duplicates."""
    seen = {c["value"] for c in chips}
    out = list(chips)
    for c in extra:
        if len(out) >= MAX_CHIPS:
            break
        if c["value"] not in seen:
            out.append(c)
            seen.add(c["value"])
    return out


# ---------- trade-off tip ----------

def field_rank(did: int, key: str, eligible_ids: list[int]) -> tuple[int, int]:
    return templates._field_rank(did, key, eligible_ids)


def tip(s) -> dict | None:
    """#1 ranks in the bottom third on a factor this profile weights among its top few: say so."""
    elig = _eligible(s.ranking)
    if len(elig) < 6:
        return None
    c = cfg()
    lead = elig[0]
    ids = [e["district_id"] for e in elig]
    n = len(ids)
    cut = max(1, round(n * c.get("tip_rank_threshold", 25) / 36))
    top = [k for k, w in sorted(s.weights.items(), key=lambda kv: -kv[1]) if w > 0][: c.get("tip_top_factors", 4)]
    for key in top:  # heaviest first: the most important weak spot
        r, of = field_rank(lead["district_id"], key, ids)
        if r >= cut:
            return {"district_id": lead["district_id"], "name": lead["name"], "field": key, "rank": r, "of": of}
    return None


def tip_text(t: dict) -> str:
    return (f"Heads up: **{t['name']}** ranks {ordinal(t['rank'])} of {t['of']} on {templates.plain_lc(t['field'])}, "
            "one of the things that matters most for this industry.")


# ---------- proactive question ----------

def _top_ids(weights: dict[str, float], constraints: dict, n: int = 3) -> list[int]:
    """Fast top-n district ids (no stability bands) for what-if checks."""
    d = scoring.dataset()
    w = scoring.weight_vector(scoring.normalize_weights(weights))
    reasons = scoring.eligibility(scoring.normalize_constraints(constraints))
    rows = np.array([i for i, r in enumerate(reasons) if r is None], dtype=int)
    order = scoring._base_order(w, rows)
    return [d.ids[i] for i in order[:n]]


def _what_if(s, field: str, label: str) -> list[int]:
    w = mod.apply(s.base_weights, {**s.modifiers, field: label}, s.slider_overrides)
    return _top_ids(w, s.constraints)


def _impact(before: list[int], after: list[int]) -> int:
    return len(set(before) ^ set(after)) + (2 if before[:1] != after[:1] else 0)


def pick_question(s, kind: str) -> dict | None:
    """The question whose answer would move the top 3 most, if any; None when it is not the moment."""
    c = cfg()
    if kind not in ASK_AFTER or not s.profile_id or not s.ranking:
        return None
    if len(s.asked_fields) >= c.get("max_questions", 3):
        return None
    if s.last_question_turn is not None and s.turn_no - s.last_question_turn < c.get("min_turn_gap", 2):
        return None
    base = _top_ids(s.weights, s.constraints)
    if not base:
        return None
    best, best_score = None, 0
    for f in QUESTIONS:
        if f in s.modifiers or f in s.slider_overrides or f in s.asked_fields:
            continue
        if s.base_weights.get(f, 0) <= 0:  # §19.1: never ask about a factor this industry ignores
            continue
        score = max(_impact(base, _what_if(s, f, "high")), _impact(base, _what_if(s, f, "low")))
        if score > best_score:
            best, best_score = f, score
    if best is None:
        return None
    return {"field": best, "text": QUESTIONS[best]["q"], "impact": best_score}


# ---------- answers ----------

def _has(norm: str, phrases: set[str]) -> bool:
    return any(re.search(rf"\b{re.escape(p)}\b", norm) for p in phrases)


def classify_answer(text: str) -> str | None:
    """'critical' | 'high' | 'low' | 'unsure' for a short answer, None if it does not read as one."""
    norm = re.sub(r"[^a-z0-9 ]", " ", nlp.normalize(text))
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm or len(norm.split()) > ANSWER_MAX_TOKENS:
        return None
    if _has(norm, UNSURE):
        return "unsure"
    if _has(norm, NEGATIVE):
        return "low"
    if _has(norm, STRONG):
        return "critical"
    if _has(norm, POSITIVE):
        return "high"
    return None


def note_for(field: str, label: str) -> str:
    return QUESTIONS[field]["yes" if label in ("high", "critical") else "no"]


def preview(s, changes: dict[str, str]) -> dict:
    """What the ranking would look like with `changes`, without changing the session."""
    w = mod.apply(s.base_weights, {**s.modifiers, **changes}, s.slider_overrides)
    after = scoring.rank(w, s.constraints, with_stability=False)["ranking"]
    names = {e["district_id"]: e["name"] for e in after}
    now = [e["district_id"] for e in _eligible(s.ranking)[:3]]
    new = [e["district_id"] for e in _eligible(after)[:3]]
    return {"before": [names[i] for i in now], "after": [names[i] for i in new],
            "leader_before": names[now[0]] if now else None, "leader_after": names[new[0]] if new else None}


def preview_text(field: str, label: str, p: dict) -> str:
    what = f"**{templates.plain(field)}** would {templates.PENDING_TEXT[label]}"
    if p["leader_after"] is None:
        tail = "No district would be eligible."
    elif p["leader_after"] != p["leader_before"]:
        tail = f"That would move **{p['leader_after']}** to #1 (now {p['leader_before']})."
    elif p["after"] != p["before"]:
        tail = f"**{p['leader_after']}** would stay #1; the top 3 would become {templates._join(p['after'])}."
    else:
        tail = "The top 3 would stay the same."
    return f"Got it: {what}. {tail}"


@lru_cache(maxsize=1)
def question_fields() -> tuple[str, ...]:
    return tuple(QUESTIONS)
