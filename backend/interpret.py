"""LLM free-text understanding (spec §18.1): a fallback when the rules cannot tell the industry, or cannot
read an answer to the advisor's question.

The LLM only fills a tiny JSON schema (an industry key or an answer label). Everything it returns is
checked against fixed lists, and the result is only ever a *suggestion* with an Apply chip: the session's
weights never change here. The rules' own reading of priorities ("water is critical") is carried over;
the LLM is not asked for priorities (it invented them in the 2026-10-03 probe)."""
from __future__ import annotations

from functools import lru_cache

from . import advisor, config, db, power, templates
from .narrate import Narrator, pick_model

# What each preset covers, in plain words. Written from the preset definitions (§6.1), not from eval phrases.
COVERS = {
    "automobile": "cars, two-wheelers, tractors, trucks, auto parts and components",
    "electronics": "electronic devices, appliances, phones, computers, chips, electrical equipment",
    "general_manufacturing": "machinery, metal goods, engineering, furniture, paper, packaging, building materials "
                             "and any other factory not listed",
    "textile": "cotton, yarn, fabric, garments, clothing, home textiles",
    "sugar_ethanol": "sugarcane crushing, sugar, jaggery, molasses, ethanol",
    "dairy": "milk and milk products",
    "food_beverages": "packaged and processed food, snacks, bakery, sweets, drinks, juices, alcoholic drinks",
    "agro_cold": "grain, pulses, flour and oil mills, spices, fruit and vegetable sorting, cold storage of farm produce",
    "pharma": "medicines, drugs, vaccines, medical consumables",
    "chemicals": "industrial chemicals, paints, fertilisers, pesticides, soaps, detergents, cosmetics",
    "plastics_rubber": "plastic and rubber products, pipes, tyres, polymers, packaging film",
    "logistics": "warehouses, transport depots, distribution and delivery hubs, e-commerce fulfilment",
}
ANSWERS = {"a_lot": "critical", "yes": "high", "no": "low", "unsure": "unsure", "not_an_answer": None}


def cfg() -> dict:
    return config.load().get("interpret", {})


@lru_cache(maxsize=1)
def preset_keys() -> dict[str, str]:
    """'dairy' -> 'preset:dairy' for the 12 presets."""
    return {p["id"].split(":", 1)[1]: p["id"] for p in db.profiles() if p["kind"] == "preset"}


@lru_cache(maxsize=1)
def industry_system() -> str:
    keys = preset_keys()
    lines = "; ".join(f"{k} = {templates.profile_name(pid)} ({COVERS.get(k, '')})" for k, pid in keys.items())
    return ("You map a business owner's message to one industry of a Maharashtra district-ranking tool. "
            f"Industries (key = name (covers)): {lines}. "
            "Use unknown if the message is not about making, processing or storing goods "
            "(for example shops, services, hospitals, schools, hotels, farming itself). Reply with JSON only.")


@lru_cache(maxsize=1)
def industry_schema() -> dict:
    return {"type": "object", "required": ["industry"],
            "properties": {"industry": {"type": "string", "enum": [*preset_keys(), "unknown"]}}}


ANSWER_SYSTEM = ("A district-ranking tool asked a business owner a yes/no question about their business. Classify "
                 "their reply: a_lot (yes, and strongly), yes, no (or only a little), unsure, or not_an_answer "
                 "(the reply is about something else entirely). Owners often answer indirectly by describing their "
                 "business; work out what that means for the question. Examples: to 'Will your plant use a lot of "
                 "water?' the reply 'we rinse every batch twice' is a_lot; to 'Will you export or import goods by "
                 "sea?' the reply 'we only sell in Pune' is no. Reply with JSON only.")
ANSWER_SCHEMA = {"type": "object", "required": ["answer"],
                 "properties": {"answer": {"type": "string", "enum": list(ANSWERS)}}}


def validate_industry(parsed: dict | None) -> str | None:
    """A preset id, or None for anything not exactly one of the 12 keys."""
    if not isinstance(parsed, dict):
        return None
    key = parsed.get("industry")
    return preset_keys().get(key) if isinstance(key, str) else None


def validate_answer(parsed: dict | None) -> str | None:
    """'critical' | 'high' | 'low' | 'unsure', or None."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("answer"), str):
        return None
    return ANSWERS.get(parsed["answer"])


def pick_interpret_model(narrator: Narrator, status) -> str | None:
    """The quality model when it is already loaded on AC (22/23 vs 17/23 for 3B on the dev set, 2026-10-03),
    else the narration model order. Never waits for a cold 7B load (~95 s)."""
    if status.wants_quality and status.quality_warm:
        return narrator.quality_model
    return pick_model(narrator, status)


def timeout() -> float:
    c = cfg()
    return float(c.get("battery_timeout_s", 45) if power.battery_mode() else c.get("timeout_s", 15))


async def ask(narrator: Narrator, status, req: dict) -> tuple[str | None, dict]:
    """Run one interpret request ({mode, text, question?}). Returns (validated result, info)."""
    if not cfg().get("enabled", True) or not config.load()["features"].get("narration", True):
        return None, {"error": "disabled"}
    model = pick_interpret_model(narrator, status) if status.reachable else None
    if model is None:
        return None, {"error": "offline"}
    n = int(cfg().get("num_predict", 40))
    text = req["text"].strip()[:400]
    if req["mode"] == "answer":
        parsed, info = await narrator.complete_json(ANSWER_SYSTEM, f"QUESTION: {req['question']}\nREPLY: {text}",
                                                    ANSWER_SCHEMA, model, n, timeout())
        return validate_answer(parsed), {**info, "raw": parsed}
    parsed, info = await narrator.complete_json(industry_system(), text, industry_schema(), model, n, timeout())
    return validate_industry(parsed), {**info, "raw": parsed}


def suggest_text(profile: str, changes: dict[str, str], current: str | None) -> str:
    s = f"Thinking about it more, this sounds like **{profile}**."
    if changes:
        s += " I'll also make " + templates._join([f"{templates.plain_lc(f)} {templates.PENDING_TEXT[lab]}"
                                                   for f, lab in changes.items()]) + "."
    if current:
        s += f" The map stays on {current} until you apply it."
    return s


def apply_result(s, turn, result: str | None) -> dict | None:
    """Turn a validated LLM result into the next assistant turn (a suggestion or a preview). Never
    changes weights. None when there is nothing useful to add, or the conversation has moved on."""
    req = turn.interpret
    if result is None or not req or s.history[-1] is not turn:
        return None
    if req["mode"] == "answer":
        from .router import answer_turn
        f = req["field"]
        if result == "unsure":
            return None
        reply, chips, fact = answer_turn(s, f, result)
        kind = "advisor_answer"
        understood = {"answer": {"field": f, "label": result, "tier": "llm"}}
    else:
        if result == s.profile_id and not req.get("modifiers"):
            return None
        changes = dict(req.get("modifiers") or {})
        s.pending_suggest, s.pending_clarify, s.pending_change = {"preset_id": result, "modifiers": changes}, None, None
        reply = suggest_text(templates.profile_name(result), changes, s.profile_name if s.profile_id != result else None)
        chips = [templates.apply_chip(result), templates.PICK_ANOTHER]
        fact = None
        kind = "suggest_preset"
        understood = {"industry": {"status": "match", "profile_id": result, "confidence": None, "tier": "llm",
                                   "candidates": [], "term": None}, "modifiers": [], "districts": []}
    bot = s.add_turn("assistant", reply, kind, fact)
    return {"turn_id": bot.turn_id, "kind": kind, "reply_md": reply, "chips": advisor.merge_chips(chips, []),
            "facts": fact, "understood": understood, "state": s.public()}
