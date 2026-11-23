"""LLM narration (spec §8): fact sheets, Ollama streaming, warm-up and the groundedness checker.

The LLM only restates a short, pre-computed fact sheet. It never decides industry, weights, scores
or who leads on a factor; those come from facts.py. Narration is optional: every turn already has a
templated reply, and any failure here becomes an SSE `error` event, never a server error.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from contextlib import aclosing
from functools import lru_cache
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from . import config, db, facts, nlp, power, templates
from .scoring import dataset

# Everything fixed lives in the system prompt: the warm-up puts it in Ollama's prompt cache, so each
# request only pays for its own fact sheet. Uncached prompt tokens cost ~33 ms each on the reference
# laptop (measured 2026-10-01, Ollama 0.34.1), so sheets are kept to roughly 60–110 tokens.
SYSTEM_PROMPT = (
    "You are a concise analyst for a Maharashtra district-ranking tool. You only restate and connect the FACTS "
    "you are given. Never introduce numbers, places or claims that are not in FACTS. If the user asks something "
    "the FACTS cannot answer, reply exactly: 'I can only answer from this tool's district data.' "
    "Maximum 3 sentences unless asked for a list.\n"
    "RULES: Use only FACTS. Quote numbers exactly as written. No other districts, no outside knowledge. "
    "Plain sentences, no headings, no lists. Scores are out of 100; pts are score points."
)
# §19.1 conversational answers to the user's questions: FACTS for anything about districts and scores, then
# (optionally) labelled general advice from the model's own knowledge, without numbers. The UI shows the part
# after "General advice:" in its own "please verify" box.
ADVISOR_PROMPT = (
    "You are a friendly site-selection advisor for small business owners in Maharashtra, using a district-ranking "
    "tool. Answer the owner's question directly in plain, simple words. For anything about districts, ranks, scores "
    "or factor values use only the FACTS, quote numbers exactly, and name no other districts; if the FACTS do not "
    "give a figure, say the tool's data does not cover it instead of estimating. Then, if it helps, add "
    "a line starting 'General advice:' with practical points from your own business knowledge (for example licences, "
    "suppliers, infrastructure the data does not cover); in General advice give no numbers and name no districts. "
    "Never turn a percentage or score into a count of people or things. Unless asked for detail: at most 3 sentences, "
    "then General advice as at most 3 short bullet points. No headings. Never mention the word FACTS and never repeat "
    "these instructions."
)
GENERAL_MARK = "General advice:"
MAX_SHEET_TOKENS = 350
WHY_RE = re.compile(r"\b(why|how come|what stops|what is stopping|not first|not top|not number one|below|behind|"
                    r"lower than|beaten|lose|loses|losing)\b")


# ---------- fact sheets ----------

@dataclass
class Sheet:
    task: str                       # explain | why_not_first | compare | chat
    text: str                       # the user message: FACTS then TASK
    districts: set[int]             # districts the sheet names
    pair: dict | None = None        # {"a","b": district ids, "overall": id|None, "leaders": {field: id|None}}
    question: str | None = None
    system: str | None = None       # None = SYSTEM_PROMPT; ADVISOR_PROMPT for conversational answers (§19.1)
    num_predict: int | None = None
    limits: tuple[float, float] | None = None  # None = the narrator's default limits

    @property
    def tokens(self) -> int:
        return approx_tokens(self.text)


def approx_tokens(text: str) -> int:
    """Upper-bound Qwen token estimate: words, single digits and punctuation each count as one."""
    return len(re.findall(r"[A-Za-z]+|\d|[^\sA-Za-z\d]", text))


def _f(x: float) -> str:
    return f"{x:.1f}"


def _eligible(ranking: list[dict]) -> list[dict]:
    return sorted((e for e in ranking if e["eligible"]), key=lambda e: e["rank"])


def _band(e: dict) -> str:
    lo, hi = e["band"]
    return f"likely #{lo}–{hi}" if lo != hi else f"stable at #{lo}"


def _short(key: str) -> str:
    """Plain factor name (§4.3), lower-cased for use inside sentences."""
    return templates.plain_lc(key)


def _importance(weights: dict) -> str:
    order = sorted((kv for kv in weights.items() if kv[1] > 0), key=lambda kv: -kv[1])
    return "Matters most: " + ", ".join(_short(k) for k, _ in order[:3]) + "."


def _pair(a_id: int, b_id: int, cmp: dict) -> dict:
    ids = {"a": a_id, "b": b_id, "tie": None}
    return {"a": a_id, "b": b_id, "overall": ids[cmp["direction"]],
            "leaders": {r["field"]: ids[r["leader"]] for r in cmp["rows"]}}


def _leads(cmp: dict, side: str, n: int = 3) -> list[dict]:
    """Weighted factors where `side` leads, biggest point difference first."""
    rows = [r for r in cmp["rows"] if r["weight"] >= facts.MIN_WEIGHT and r["leader"] == side]
    return sorted(rows, key=lambda r: -abs(r["a"]["points"] - r["b"]["points"]))[:n]


def _better(name: str, rows: list[dict], other: str | None = None, tail: str = "") -> str:
    """'Kolhapur is better on water, estates.' Chosen by an A/B test on the 14 compare/why prompts
    (2026-10-01): with 'X beats Y on: …' lists for both sides the 3B model inverted 7 of 14 answers,
    describing the lower district's own advantages as its weaknesses."""
    vs = f" than {other}" if other else ""
    if not rows:
        return f"{name} is better{vs} on no weighted factor."
    return f"{name} is better{vs} on " + ", ".join(_short(r["field"]) for r in rows) + tail + "."


def _compose(fact_lines: list[str], task_line: str) -> str:
    return "FACTS:\n" + "\n".join(fact_lines) + "\nTASK: " + task_line


def _not_ranked(e: dict, profile: str) -> str:
    return f"{e['name']} is not ranked for {profile}: {e['reason']}."


def _values(items: list[dict], n: int) -> str:
    return ", ".join(templates.valued(x["field"], x["display"]) for x in items[:n])


def explain_sheet(did: int, ranking: list[dict], weights: dict, profile: str, focus: list[str] | None = None) -> Sheet:
    e = next(x for x in ranking if x["district_id"] == did)
    elig = _eligible(ranking)
    name, names = e["name"], {did}
    if not e["eligible"]:
        lines = [_not_ranked(e, profile)]
        task = f"Why is {name} not ranked? At most 2 sentences."
    else:
        lines = [f"{name}: #{e['rank']} of {len(elig)} for {profile}, score {_f(e['score'])}, confidence {e['confidence']}."]
        task = f"Why is {name} #{e['rank']}? At most 3 sentences."
    st, wk = facts.strengths(did, weights), facts.weaknesses(did, weights)
    lines.append(f"Strengths: {_values(st, 2)}." if st else "Strengths: none.")
    if wk:
        lines.append(f"Weakness: {_values(wk, 1)}.")
    for key in focus or []:
        v = next(x for x in facts.district_values(did, weights) if x["field"] == key)
        lines.append(f"{v['label']}: {v['display']}.")
    pair = None
    if e["eligible"] and len(elig) > 1:
        other = elig[1] if e["rank"] == 1 else elig[0]
        cmp = facts.compare(did, other["district_id"], ranking, weights)
        if e["rank"] == 1:
            lines.append(f"Runner-up {other['name']} {_f(other['score'])}, {_f(e['score'] - other['score'])} pts behind. "
                         + _better(name, _leads(cmp, "a", 2), other["name"]))
        else:
            lines.append(f"Leader {other['name']} {_f(other['score'])}, {_f(other['score'] - e['score'])} pts ahead. "
                         + _better(other["name"], _leads(cmp, "b", 2), name))
        names.add(other["district_id"])
        pair = _pair(did, other["district_id"], cmp)
        # Explain A/B (10 prompts x 3, 2026-10-02): 24/30 grounded as is, 25/30 with the first clause,
        # 28/30 with both; the model otherwise invented figures and factors for the other district.
        task += f" Name only the factors listed; give no figures for {other['name']}."
    return Sheet("explain", _compose(lines, task), names, pair)


def _pair_lines(cmp: dict, profile: str, lower_side: bool = True) -> tuple[list[str], dict, dict]:
    """Score line, the higher district's advantages and (for compare) the lower one's. Why-sheets
    leave the lower district's advantages out: the question is what puts the other one ahead."""
    a, b = cmp["a"], cmp["b"]
    upper, lower, us, ls = (a, b, "a", "b") if cmp["direction"] != "b" else (b, a, "b", "a")
    if cmp["direction"] == "tie":
        head = f"{profile}: {a['name']} #{a['rank']} and {b['name']} #{b['rank']} are level at {_f(a['score'])}."
    else:
        head = (f"{profile}: {upper['name']} #{upper['rank']} ({_f(upper['score'])}) is {_f(abs(cmp['score_diff']))} pts "
                f"above {lower['name']} #{lower['rank']} ({_f(lower['score'])}).")
    lines = [head, _better(upper["name"], _leads(cmp, us))]
    low = _leads(cmp, ls)
    if lower_side and low:
        lines.append(_better(lower["name"], low, tail=", but these count for less"))
    return lines, upper, lower


def why_sheet(a_id: int, b_id: int, ranking: list[dict], weights: dict, profile: str) -> Sheet:
    """Why one of two districts ranks below the other."""
    cmp = facts.compare(a_id, b_id, ranking, weights)
    a, b = cmp["a"], cmp["b"]
    if not a["eligible"] or not b["eligible"]:
        x = a if not a["eligible"] else b
        lines, task = [_not_ranked(x, profile)], f"Why is {x['name']} not ranked? At most 2 sentences."
    else:
        lines, upper, lower = _pair_lines(cmp, profile, lower_side=False)
        task = f"Why is {lower['name']} below {upper['name']}? At most 3 sentences."
    return Sheet("why_not_first", _compose(lines, task), {a_id, b_id}, _pair(a_id, b_id, cmp))


def compare_sheet(a_id: int, b_id: int, ranking: list[dict], weights: dict, profile: str) -> Sheet:
    cmp = facts.compare(a_id, b_id, ranking, weights)
    a, b = cmp["a"], cmp["b"]
    if not a["eligible"] or not b["eligible"]:
        lines = [_not_ranked(x, profile) if not x["eligible"] else f"{x['name']} is #{x['rank']} ({_f(x['score'])})."
                 for x in (a, b)]
    else:
        lines, _, _ = _pair_lines(cmp, profile)
    # The last sentence cut wrong factor claims from 3/24 to 0/24 in a repeated A/B (2026-10-01, STAGE_LOG).
    task = (f"2-sentence takeaway comparing {a['name']} and {b['name']}; a full table is shown separately. "
            "Name only the factors listed for each district.")
    return Sheet("compare", _compose(lines, task), {a_id, b_id}, _pair(a_id, b_id, cmp))


CHANGE_WORDS = templates.CHANGE_TEXT


def chat_sheet(ranking: list[dict], weights: dict, excluded: list[dict], profile: str, question: str | None,
               mentioned: list[int] | None = None, changes: dict | None = None, leader_before: str | None = None,
               tip: dict | None = None) -> Sheet:
    elig = _eligible(ranking)
    if elig:
        top = ", ".join(f"{e['rank']} {e['name']} {_f(e['score'])}" for e in elig[:5])
        lines = [f"{profile}: {len(elig)} districts ranked" + (f", {len(excluded)} excluded" if excluded else "")
                 + f". Top 5: {top}."]
    else:
        lines = [f"{profile}: no district is eligible."]
    names = {e["district_id"] for e in elig[:5]}
    if elig:
        st = facts.strengths(elig[0]["district_id"], weights)[:3]
        if st:
            lines.append(f"{elig[0]['name']} strengths: " + ", ".join(_short(s["field"]) for s in st) + ".")
    for did in mentioned or []:
        if did not in names:
            e = next(x for x in ranking if x["district_id"] == did)
            lines.append(f"{e['name']} is #{e['rank']} ({_f(e['score'])})." if e["eligible"] else _not_ranked(e, profile))
            names.add(did)
    lines.append(_importance(weights))
    if changes:
        lines.append("Just changed: " + ", ".join(f"{_short(f)} {CHANGE_WORDS[l]}" for f, l in changes.items()) + ".")
        if leader_before and elig and leader_before != elig[0]["name"]:
            lines.append(f"{elig[0]['name']} now leads (was {leader_before}).")
    if tip:  # §18.2 trade-off tip: the leader's weak spot on an important factor
        lines.append(f"Weak spot: {tip['name']} is only #{tip['rank']} of {tip['of']} on {_short(tip['field'])}.")
    if question:
        task = f"Answer in at most 3 sentences: \"{question.strip()[:200]}\""
    else:
        task = "In at most 2 sentences, say what puts the leaders on top."
    return Sheet("chat", _compose(lines, task), names, None, question)


def advisor_sheet(ranking: list[dict], weights: dict, profile: str, question: str, notes: list[str] | None = None,
                  needs: list[str] | None = None, tip: dict | None = None, detail: bool = False) -> Sheet:
    """§19.1 facts for a conversational answer: the ranking, what matters, the leader's strengths and weak spots,
    what the owner told the advisor and the industry's curated needs the data does not cover."""
    elig = _eligible(ranking)
    lines = []
    if elig:
        lines.append(f"{profile}: top 5 of {len(elig)}: " + ", ".join(f"{e['rank']} {e['name']} {_f(e['score'])}"
                                                                   for e in elig[:5]) + ".")
        lead = elig[0]
        st, wk = facts.strengths(lead["district_id"], weights), facts.weaknesses(lead["district_id"], weights)
        if st:
            lines.append(f"{lead['name']} strengths: {_values(st, 3)}.")
        if wk:  # with values, so "land 0.10 (expensive)" never reads as "cheap land" being a risk
            lines.append(f"{lead['name']} weak spots: {_values(wk, 2)}.")
    lines.append(_importance(weights))
    low = [k for k, v in sorted(weights.items(), key=lambda kv: kv[1]) if v < facts.MIN_WEIGHT]
    if low:
        lines.append("Counts little or not at all: " + ", ".join(_short(k) for k in low[:5]) + ".")
    if tip:
        lines.append(f"Weak spot: {tip['name']} is only #{tip['rank']} of {tip['of']} on {_short(tip['field'])}.")
    if notes:
        lines.append("The owner said: " + "; ".join(n.lower() for n in notes[:4]) + ".")
    if needs:
        lines.append("Not in the data, worth checking for this industry: " + "; ".join(n.lower() for n in needs[:5]) + ".")
    lines.append("The data covers 13 factors: skilled workers, roads, highway, rail, port, electricity, water, farm "
                 "produce, crop variety, irrigation, industrial areas, small suppliers, land price.")
    q = question.strip()[:300].replace('"', "'")
    if detail:
        task = f'Answer this question in detail (up to 6 sentences, then up to 5 General advice bullets): "{q}"'
    else:
        task = f'Answer this question: "{q}"'
    names = {e["district_id"] for e in elig[:5]}
    n = config.load()["narration"]
    # its own system prompt is often not cached (notes use SYSTEM_PROMPT), so the first token needs longer
    lim = (float(n.get("ask_first_token_timeout_s", 15)), float(n.get("ask_total_timeout_s", 60)))
    return Sheet("ask", _compose(lines, task), names, None, question, ADVISOR_PROMPT, 260 if detail else 170, lim)


def build_sheet(ctx: dict, detail: bool = False) -> Sheet | None:
    """Turn context (stored by the router at message time) → fact sheet."""
    kind, f = ctx["kind"], ctx.get("facts") or {}
    rk, w, prof = ctx["ranking"], ctx["weights"], ctx["profile"]
    q = ctx.get("question") or ""
    if kind == "ask" or (kind == "chat" and q):
        return advisor_sheet(rk, w, prof, q, ctx.get("notes"), ctx.get("needs"), ctx.get("tip"), detail)
    if kind == "compare":
        a, b = f["compare"]["a"]["district_id"], f["compare"]["b"]["district_id"]
        if WHY_RE.search(nlp.normalize(q)):
            return why_sheet(a, b, rk, w, prof)
        return compare_sheet(a, b, rk, w, prof)
    if kind == "explain_district":
        did = f["district_id"]
        e = next(x for x in rk if x["district_id"] == did)
        if e["eligible"] and e["rank"] != 1 and WHY_RE.search(nlp.normalize(q)):
            first = next(x for x in rk if x["rank"] == 1)
            return why_sheet(did, first["district_id"], rk, w, prof)
        return explain_sheet(did, rk, w, prof, ctx.get("focus"))
    if kind == "chat":
        return chat_sheet(rk, w, ctx.get("excluded", []), prof, q, ctx.get("mentioned"))
    if kind in ("apply_preset", "current_preset", "clarify_answer", "adjust"):
        before = ctx.get("leader_before") if kind == "adjust" else None
        return chat_sheet(rk, w, ctx.get("excluded", []), prof, None, None, f.get("changes"), before, ctx.get("tip"))
    return None


# ---------- groundedness (§8.3) ----------

NUM_RE = re.compile(r"\d[\d,]*\.?\d*")
# Comparative cue words. "Advantage" words attach to the factors around them ("its literacy advantage");
# comparatives can also be about the overall score ("scores 9.4 points higher").
CMP_POS = {"higher", "more", "better", "stronger", "greater", "larger", "bigger", "ahead", "leads", "lead", "leading",
           "outperforms", "outperform", "outperforming", "outscores", "outscoring", "beats", "beating", "superior",
           "edge", "advantage", "advantages", "cheaper", "closer", "above"}
CMP_NEG = {"lower", "less", "weaker", "worse", "poorer", "smaller", "fewer", "behind", "trails", "trail", "trailing", "lags",
           "lag", "lagging", "costlier", "farther", "below", "disadvantage", "disadvantages", "lacks", "lack",
           "lacking"}
NEGATORS = {"not", "no", "without", "lack", "lacks", "lacking"}
RANK_CUES = {"above", "below", "outperforms", "outperform", "outperforming", "outscores", "outscoring", "beats",
             "beating", "trails", "trailing", "lags", "lagging"}
SCORE_WORDS = {"score", "scores", "scored", "overall", "rank", "ranks", "ranked", "ranking", "points", "pts"}
CLAUSE_SPLIT = re.compile(r";|,? while |,? whereas |,? but |,? although |,? though |,? despite |,? however |"
                          r",? yet |\. ")
PRICE_FIELD_FLIP = {"land": {"expensive", "costlier", "pricier"}}  # "more expensive land" = worse affordability
ATTACH = 8      # max tokens between a factor group and the cue word it belongs to
LIST_GAP = 3    # max tokens between two factors of one list (", ", "and", "&")
NEG_WINDOW = 6  # a negator this close before a cue's factor list flips it ("lack of … estates and power advantages")
MSME_RE = re.compile(r"\bmicro,?\s+small,?\s+(and\s+)?medium(-sized)?\s+enterprises\b", re.I)
PRONOUNS = {"it", "its"}
WEIGHT_WORDS = {"weight", "weighted", "weightage", "important", "significant", "influential", "relevant", "impact"}
OBJECT_MARKERS = {"than", "to", "over", "vs", "versus", "against", "with", "from", "beyond", "of"}
RELATIVES = {"which", "who", "where", "whose"}
NOUN_CUES = {"advantage", "advantages", "edge", "lead", "disadvantage", "disadvantages", "superior"}
NEUTRAL_CUES = {"strength", "strengths", "weakness", "weaknesses", "strong", "weak"}


def _numbers(text: str) -> list[str]:
    return [m.rstrip(".,") for m in NUM_RE.findall(text) if m.rstrip(".,")]


def _num_ok(tok: str, allowed: list[float]) -> bool:
    try:
        x = float(tok.replace(",", ""))
    except ValueError:
        return True
    dec = len(tok.split(".")[1]) if "." in tok else 0
    last = 10 ** -dec
    return any(x == y or abs(x - y) <= 0.05 * abs(y) or abs(x - y) <= last + 1e-9 for y in allowed)


def _district_ids(text: str) -> list[int]:
    from .router import find_districts
    ids, _ = find_districts(nlp.tokenize(text))
    return ids


def _mentions(tokens: list[str]) -> list[tuple[int, int, bool]]:
    """(position, district id, possessive) per district mention. "Sangli's 81.7" is not a possessive owner."""
    from .router import district_matcher
    out = []
    for m in district_matcher().find(tokens):
        poss = m.end < len(tokens) - 1 and tokens[m.end] == "s" and not tokens[m.end + 1].isdigit()
        out.append((m.start, m.values[0], poss))
    return sorted(out)


@lru_cache(maxsize=1)
def _factor_matcher() -> nlp.PhraseMatcher:
    """Factor names as the model writes them: registry label and short name plus all chat synonyms."""
    m = nlp.PhraseMatcher()
    syn = nlp.field_synonyms()
    for f in db.fields():
        for phrase in {f["label"], f["short"], f["plain_label"], *syn.get(f["key"], [])}:
            m.add(phrase, "field", f["key"])
    return m


def _field_groups(tokens: list[str], stops: set[int]) -> list[tuple[int, int, list[str]]]:
    """Factors in a list ("rail, estates and power") form one group: (first pos, last pos, fields).
    A cue word or district mention between two factors ends the group."""
    found = sorted((m.start, m.end, v) for m in _factor_matcher().find(tokens) for v in m.values)
    groups: list[tuple[int, int, list[str]]] = []
    for start, end, f in found:
        if groups and start - groups[-1][1] <= LIST_GAP and not any(groups[-1][1] < s < start for s in stops):
            g = groups[-1]
            groups[-1] = (g[0], end - 1, g[2] + ([f] if f not in g[2] else []))
        else:
            groups.append((start, end - 1, [f]))
    return groups


def _weighting(toks: list[str], i: int) -> bool:
    """'less weight', 'less important', 'count for less': a remark about weights, not about a district."""
    return (i + 1 < len(toks) and toks[i + 1] in WEIGHT_WORDS) or (i > 0 and toks[i - 1] == "for")


def _direction_claims(text: str, pair: dict) -> list[dict]:
    """Comparative claims about the pair, each checked against facts.compare leaders.

    Each factor group attaches to its nearest cue word in the clause; "strengths"/"weaknesses" also
    capture factors but make no comparative claim. A possessive district between a cue and a group
    breaks the link ("advantages over Raigad's land"). A cue's owner is the possessive district or
    "its" before it, else the clause's first district; the other side is the pair's other district.
    A negator ("not", "lack of") before a cue flips it. Rank cues, or comparatives next to score
    words with no factor attached, are claims about the overall score."""
    claims = []
    a, b = pair["a"], pair["b"]
    text = MSME_RE.sub("msme", text)
    last_subject = None
    for sent in re.split(r"(?<=[.!?])\s+", text):
        sent_ids = [i for i in _district_ids(sent) if i in (a, b)]
        stoks = nlp.tokenize(sent)
        # "It lags…", "The district lags…": the sentence continues the previous subject
        pronoun_start = bool(stoks) and (stoks[0] in PRONOUNS or stoks[:2] in (["the", "district"], ["this", "district"]))
        if not sent_ids and not (pronoun_start and last_subject):
            continue
        # "It lags behind Nashik": a sentence opening with a pronoun continues the previous subject
        if pronoun_start and last_subject:
            sent_subject = last_subject
        else:
            sm = [(p, d) for p, d, _ in _mentions(stoks) if d in (a, b)
                  and not (p > 0 and (stoks[p - 1] in OBJECT_MARKERS or stoks[p - 1] in RANK_CUES))]
            first_pos = min((p for p, _, _ in _mentions(stoks)), default=len(stoks))
            if last_subject and any(t in PRONOUNS for t in stoks[:first_pos]):
                sent_subject = last_subject  # "This is due to …, and it lags behind … Nashik"
            else:
                sent_subject = sm[0][1] if sm else sent_ids[0]
        last_subject = sent_subject
        for clause in CLAUSE_SPLIT.split(sent):
            if not clause or not clause.strip():
                continue
            toks = nlp.tokenize(clause)
            ments = [m for m in _mentions(toks) if m[1] in (a, b)]
            cues = [i for i, t in enumerate(toks) if (t in CMP_POS or t in CMP_NEG) and not _weighting(toks, i)]
            if not cues:
                continue
            anchors = sorted(cues + [i for i, t in enumerate(toks) if t in NEUTRAL_CUES])
            # "while better near a port, scores 92.9, slightly below Pune": a district that is only an
            # object in this clause is not its subject; fall back to the sentence's subject
            subjects = [d for p, d, _ in ments if not (p > 0 and (toks[p - 1] in OBJECT_MARKERS or toks[p - 1] in CMP_POS
                                                                   or toks[p - 1] in CMP_NEG))]
            first = subjects[0] if subjects else sent_subject
            poss = {p: d for p, d, s in ments if s}
            stops = set(anchors) | {p for p, _, _ in ments} | {i for i, t in enumerate(toks) if t == "for"}

            attached: dict[int, list[str]] = {c: [] for c in anchors}
            reach: dict[int, int] = {c: c for c in anchors}  # earliest token of the factors attached to a cue
            for g0, g1, fs in _field_groups(toks, stops):
                if g0 > 0 and toks[g0 - 1] == "for" and not (g0 > 1 and toks[g0 - 2] in CMP_POS | CMP_NEG):
                    continue  # a purpose ("cheap land for agriculture"), not a factor being compared;
                    # "scores higher for cheap land" (cue right before "for") does compare it (2026-10-04)
                best, bd = None, None
                for c in anchors:
                    lo, hi = (c, g0) if c < g0 else (g1, c)
                    d = hi - lo
                    # ", and" before a cue starts a new coordinated phrase: "close to suppliers, and outperforming
                    # Thane in skilled workers" claims nothing about suppliers
                    coord = c > g1 and any(toks[q] == "," and toks[q + 1] == "and" for q in range(g1 + 1, c - 1))
                    if d <= ATTACH and not coord and not any(lo < p < hi for p in poss) \
                            and not any(lo < x < hi for x in anchors):
                        if bd is None or d < bd:
                            best, bd = c, d
                if best is not None:
                    attached[best] += [f for f in fs if f not in attached[best]]
                    reach[best] = min(reach[best], g0)

            starts = [g0 for g0, _, _ in _field_groups(toks, stops)]
            prev = -1
            for c in cues:
                owner = None
                # "the land advantage of Amravati" names the owner; "ahead of Pune" names the other side
                after = [d for p, d, _ in ments if p == c + 2 and toks[c + 1] == "of" and toks[c] in NOUN_CUES]
                if after:
                    owner = after[0]  # "the land advantage of Amravati"
                # the relative pronoun must follow the district directly ("Pune, which"), not a noun ("its port, which")
                # and it owns every cue up to the next district ("Nashik, which scores higher, … better in X")
                rel = [d for p, d, _ in ments if p < c and any(toks[q] in RELATIVES and all(t == "," for t in toks[p + 1:q])
                                                             for q in range(p + 1, min(p + 3, c)))
                       and not any(p < p2 < c for p2, _, _ in ments)]
                if owner is None and rel:
                    owner = rel[-1]  # "compared to Pune, which has better …"
                for p in range(c - 1, prev, -1):
                    if owner is not None:
                        break
                    if p in poss:
                        owner = poss[p]
                        break
                    if toks[p] == "its" and (any(p < x <= p + 3 for x in cues) or
                                             any(p < g <= p + 3 for g in starts)):
                        owner = sent_subject  # "its port advantage"; not "Pune, its runner-up, scores less"
                        break
                if owner is None:
                    # nearest district before the cue that is not an object ("than X", "outperforms X")
                    subj = [d for p, d, _ in ments if p < c and not (p > 0 and (toks[p - 1] in OBJECT_MARKERS
                                                                                  or p - 1 in cues))]
                    has_pron = any(t in PRONOUNS for t in toks[:c]) or (pronoun_start and toks[:2] == stoks[:2])
                    owner = subj[-1] if subj else (sent_subject if has_pron else first)
                other = b if owner == a else a
                positive = toks[c] in CMP_POS
                window = range(max(0, reach[c] - NEG_WINDOW), c)
                if any(toks[p] in NEGATORS and not (p in attached and attached[p]) for p in window):
                    positive = not positive
                claimed = owner if positive else other
                ts = set(toks)
                for f in attached[c]:
                    cl = claimed
                    if ts & PRICE_FIELD_FLIP.get(f, set()):
                        cl = other if cl == owner else owner
                    claims.append({"clause": clause.strip(), "field": f, "claimed": cl, "actual": pair["leaders"].get(f)})
                near = set(toks[max(0, c - 4):c + 7])
                if not attached[c] and toks[c] not in NEGATORS and (toks[c] in RANK_CUES or near & SCORE_WORDS):
                    claims.append({"clause": clause.strip(), "field": None, "claimed": claimed, "actual": pair["overall"]})
                prev = c
    return claims


def check(output: str, sheet: Sheet) -> dict:
    out = output.strip()
    if templates.REFUSAL.rstrip(".").lower() in out.lower():
        return {"grounded": True, "refusal": True, "unsupported_numbers": [], "unknown_districts": [],
                "wrong_directions": [], "numbers_total": 0, "numbers_supported": 0}
    allowed = [100.0] + [float(n.replace(",", "")) for n in _numbers(sheet.text)]  # scores are out of 100
    nums = _numbers(out)
    bad_nums = [n for n in nums if not _num_ok(n, allowed)]
    sheet_ids = sheet.districts | set(_district_ids(sheet.text))
    unknown = [i for i in _district_ids(out) if i not in sheet_ids]
    wrong = []
    if sheet.pair:
        for c in _direction_claims(out, sheet.pair):
            if c["claimed"] != c["actual"]:
                wrong.append(c["clause"])
    d = {x["id"]: x["name"] for x in _districts()}
    return {"grounded": not bad_nums and not unknown and not wrong, "refusal": False,
            "unsupported_numbers": bad_nums, "unknown_districts": [d[i] for i in unknown],
            "wrong_directions": wrong, "numbers_total": len(nums), "numbers_supported": len(nums) - len(bad_nums)}


def _districts() -> list[dict]:
    return db.districts()


# ---------- Ollama client ----------

@dataclass
class Narrator:
    base_url: str
    model: str
    fallback_model: str
    quality_model: str
    keep_alive: str
    options: dict
    first_token_timeout: float
    total_timeout: float
    battery_first_token_timeout: float = 30.0
    battery_total_timeout: float = 75.0
    client: httpx.AsyncClient | None = None
    warming: bool = field(default=False)
    lane: asyncio.Lock | None = None  # one Ollama request at a time (§18.4): notes, interpret and brief share the CPU
    warming_quality: bool = field(default=False)

    @classmethod
    def from_config(cls) -> "Narrator":
        c = config.load()
        o, n = c["ollama"], c["narration"]
        return cls(o["base_url"], o["narration_model"], o["fallback_model"], o["quality_model"], o["keep_alive"],
                   {k: n[k] for k in ("num_ctx", "num_predict", "temperature", "top_p")},
                   o["first_token_timeout_s"], o["total_timeout_s"],
                   o.get("battery_first_token_timeout_s", 30.0), o.get("battery_total_timeout_s", 75.0))

    def quality_limits(self) -> tuple[float, float]:
        """§19.5 the 7B 'Tell me more' answer: room for a cold load and ~3.5 tokens/s."""
        o = config.load()["ollama"]
        return float(o.get("quality_first_token_timeout_s", 60)), float(o.get("quality_total_timeout_s", 180))

    def limits(self) -> tuple[float, float]:
        """(first-token, total) timeouts in seconds; longer in battery mode, where the CPU is throttled."""
        if power.battery_mode():
            return (max(self.first_token_timeout, self.battery_first_token_timeout),
                    max(self.total_timeout, self.battery_total_timeout))
        return self.first_token_timeout, self.total_timeout

    def _client(self) -> httpx.AsyncClient:
        # One shared client: a new client per call costs ~0.5 s of setup on Windows (see STAGE_LOG P4).
        if self.client is None or self.client.is_closed:
            # Read timeout covers the slowest case (battery); stream() enforces the per-mode limits.
            read = max(self.total_timeout, self.battery_total_timeout)
            self.client = httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(read, connect=1.0))
        return self.client

    async def aclose(self) -> None:
        if self.client is not None:
            await self.client.aclose()

    def _lane(self) -> asyncio.Lock:
        # Created lazily inside the running loop (tests run each Narrator in its own asyncio.run loop).
        if self.lane is None:
            self.lane = asyncio.Lock()
        return self.lane

    async def _acquire(self, timeout: float) -> bool:
        """Wait for the shared lane; False when another request held it for longer than `timeout`."""
        try:
            await asyncio.wait_for(self._lane().acquire(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def complete_json(self, system: str, user: str, schema: dict, model: str, num_predict: int,
                            timeout: float) -> tuple[dict | None, dict]:
        """One non-streaming call constrained to a JSON schema (Ollama `format`). Returns (parsed, info);
        parsed is None on any failure and info["error"] says why. Never raises."""
        t0 = time.perf_counter()
        if not await self._acquire(timeout):
            return None, {"error": "busy", "ms": round((time.perf_counter() - t0) * 1000)}
        try:
            t1 = time.perf_counter()
            opts = {**self.options, "num_predict": num_predict, "temperature": 0}
            body = {"model": model, "stream": False, "keep_alive": self.keep_alive, "format": schema, "options": opts,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            left = max(0.1, timeout - (t1 - t0))
            # httpx's own timeout ends a slow Ollama call cleanly (ReadTimeout); wait_for is only a backstop
            r = await asyncio.wait_for(self._client().post("/api/chat", json=body,
                                                           timeout=httpx.Timeout(left, connect=1.0)),
                                       timeout=left + 1.0)
            if r.status_code != 200:
                return None, {"error": f"http {r.status_code}"}
            msg = r.json()
            parsed = json.loads((msg.get("message") or {}).get("content") or "")
            info = {"ms": round((time.perf_counter() - t0) * 1000), "wait_ms": round((t1 - t0) * 1000),
                    "model": model, "prompt_tokens": msg.get("prompt_eval_count"), "gen_tokens": msg.get("eval_count")}
            return (parsed if isinstance(parsed, dict) else None), info
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return None, {"error": "timeout", "ms": round((time.perf_counter() - t0) * 1000)}
        except (httpx.HTTPError, OSError, ValueError):
            return None, {"error": "offline", "ms": round((time.perf_counter() - t0) * 1000)}
        finally:
            self._lane().release()

    def body(self, model: str, user: str, num_predict: int | None = None, system: str | None = None) -> dict:
        opts = dict(self.options)
        if num_predict is not None:
            opts["num_predict"] = num_predict
        return {"model": model, "stream": True, "keep_alive": self.keep_alive, "options": opts,
                "messages": [{"role": "system", "content": system or SYSTEM_PROMPT}, {"role": "user", "content": user}]}

    async def warm_up(self, status) -> bool:
        """One-token generation with the real system prompt and options, so the model and the
        system-prompt prefix are loaded before the first user request (a cold load took 28–37 s)."""
        if self.warming:
            return False
        self.warming = True
        try:
            model = pick_model(self, status) or self.model
            async with self._client().stream("POST", "/api/chat", json=self.body(model, "Say OK.", 1),
                                             timeout=httpx.Timeout(180, connect=1.0)) as r:
                r.raise_for_status()
                async for _ in r.aiter_lines():
                    pass
            status.warm = True
            return True
        except (httpx.HTTPError, OSError):
            status.warm = False
            return False
        finally:
            self.warming = False

    async def warm_quality(self, status, system: str) -> bool:
        """Load the quality model in the background with the interpret system prompt cached (§18.1).
        A cold 7B load took ~95 s on the reference laptop. Does not take the lane: chat notes keep running."""
        if self.warming_quality:
            return False
        self.warming_quality = True
        try:
            body = {"model": self.quality_model, "stream": False, "keep_alive": self.keep_alive,
                    "options": {**self.options, "num_predict": 1},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": "dairy"}]}
            r = await self._client().post("/api/chat", json=body, timeout=httpx.Timeout(300, connect=1.0))
            r.raise_for_status()
            status.loaded = status.loaded | {self.quality_model}
            return True
        except (httpx.HTTPError, OSError):
            return False
        finally:
            self.warming_quality = False

    async def stream(self, prompt: str, model: str, num_predict: int | None = None,
                     limits: tuple[float, float] | None = None, system: str | None = None) -> AsyncIterator[tuple[str, dict]]:
        """Yields ("token", {t}) … then ("done", {...}) or ("error", {message}). Never raises.
        Waits for the shared lane first (up to the total timeout); the timeouts start once it has it.
        Callers wrap it in `aclosing` so an abandoned stream releases the lane at once."""
        first_token_timeout, total_timeout = limits or self.limits()
        w0 = time.perf_counter()
        if not await self._acquire(total_timeout):
            yield "error", {"message": "AI note unavailable (busy)"}
            return
        try:
            async for ev in self._stream(prompt, model, num_predict, first_token_timeout, total_timeout,
                                         round((time.perf_counter() - w0) * 1000), system):
                yield ev
        finally:
            self._lane().release()

    async def _stream(self, prompt: str, model: str, num_predict: int | None, first_token_timeout: float,
                      total_timeout: float, wait_ms: int, system: str | None = None) -> AsyncIterator[tuple[str, dict]]:
        t0 = time.perf_counter()
        deadline = t0 + total_timeout
        first_ms = None
        text: list[str] = []
        stats: dict = {}
        try:
            async with self._client().stream("POST", "/api/chat", json=self.body(model, prompt, num_predict, system)) as r:
                if r.status_code != 200:
                    await r.aread()
                    yield "error", {"message": f"AI note unavailable (Ollama HTTP {r.status_code})"}
                    return
                lines = r.aiter_lines()
                while True:
                    now = time.perf_counter()
                    limit = (t0 + first_token_timeout if first_ms is None else deadline) - now
                    if limit <= 0:
                        raise asyncio.TimeoutError
                    try:
                        line = await asyncio.wait_for(anext(lines), timeout=limit)
                    except StopAsyncIteration:
                        break
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        yield "error", {"message": f"AI note unavailable ({msg['error']})"}
                        return
                    tok = (msg.get("message") or {}).get("content", "")
                    if tok:
                        if first_ms is None:
                            first_ms = (time.perf_counter() - t0) * 1000
                        text.append(tok)
                        yield "token", {"t": tok}
                    if msg.get("done"):
                        stats = {"load_ms": round(msg.get("load_duration", 0) / 1e6),
                                 "prompt_tokens": msg.get("prompt_eval_count"),
                                 "prompt_ms": round(msg.get("prompt_eval_duration", 0) / 1e6),
                                 "gen_tokens": msg.get("eval_count")}
                        break
        except asyncio.TimeoutError:
            yield "error", {"message": "AI note unavailable (timeout)", "partial": "".join(text)}
            return
        except (httpx.HTTPError, OSError, ValueError):
            yield "error", {"message": "AI note unavailable (offline)"}
            return
        yield "done", {"text": "".join(text), "model": model, "ms": round((time.perf_counter() - t0) * 1000),
                       "first_token_ms": None if first_ms is None else round(first_ms), "wait_ms": wait_ms,
                       "ollama": stats}


def pick_model(narrator: Narrator, status, quality: bool = False) -> str | None:
    """Narration model if present, else the fallback (the fallback first in battery mode); the quality
    model only when asked and present."""
    have = status.models
    if quality and status._present(narrator.quality_model, have):
        return narrator.quality_model
    order = (narrator.fallback_model, narrator.model) if power.battery_mode() else (narrator.model, narrator.fallback_model)
    for m in order:
        if status._present(m, have):
            return m
    return None


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def narrate_events(narrator: Narrator, status, turn, quality: bool = False) -> AsyncIterator[str]:
    """SSE body for /api/narrate. Stores the narration and its grounding on the turn."""
    if not config.load()["features"].get("narration", True):
        yield sse("error", {"message": "AI note unavailable (disabled)"})
        return
    if turn.ctx is None:
        yield sse("error", {"message": "AI note unavailable for this reply"})
        return
    model = pick_model(narrator, status, quality) if status.reachable else None
    if model is None:
        yield sse("error", {"message": "AI note unavailable (offline)"})
        return
    sheet = build_sheet(turn.ctx, detail=quality)
    if sheet is None:
        yield sse("error", {"message": "AI note unavailable for this reply"})
        return
    kw = {k: v for k, v in (("num_predict", sheet.num_predict), ("system", sheet.system),
                            ("limits", narrator.quality_limits() if model == narrator.quality_model else
                             tuple(max(a, b) for a, b in zip(sheet.limits, narrator.limits())) if sheet.limits else None))
          if v is not None}  # a plain note keeps the original two-argument call
    async with aclosing(narrator.stream(sheet.text, model, **kw)) as events:
        async for ev, data in events:
            if ev == "done":
                grounding = check(data["text"], sheet)
                rec = {"text": data["text"], "grounding": grounding, "model": model}
                if quality:
                    turn.narration_detail = rec  # §19.1 "Tell me more" keeps the short answer too
                else:
                    turn.narration = rec
                status.warm = True
                yield sse("done", {"grounding": grounding, "model": model, "ms": data["ms"],
                                   "first_token_ms": data["first_token_ms"], "wait_ms": data.get("wait_ms", 0),
                                   "task": sheet.task, "ollama": data["ollama"]})
            else:
                yield sse(ev, data)
