"""Templated replies (spec §8.4). Instant, deterministic, built only from the facts layer.
These are always shown first; LLM narration only ever adds to them.

Factors are named with their plain labels (§4.3) and weight changes are described in words, never
as percentages (§5.5): users see "Water supply now matters most", not "water 31%"."""
from __future__ import annotations

from . import db, facts
from .scoring import dataset

REFUSAL = "I can only answer from this tool's district data."
CHANGE_TEXT = {"critical": "now matters most", "high": "now matters more", "low": "now matters less",
               "exclude": "is now ignored"}
PENDING_TEXT = {"critical": "matter most", "high": "matter more", "low": "matter less", "exclude": "be ignored"}

HELP_CHIPS = [
    "Food processing plant, water is critical",
    "Automobile plant with good power",
    "Textile mill",
    "Pharmaceutical factory",
    "Compare Pune and Nashik",
    "Cement plant",
]
PICK_ANOTHER = {"label": "Pick another", "value": "action:pick_preset"}
PICK_INDUSTRY = {"label": "Pick an industry", "value": "action:pick_preset"}
SAVE_CHIP = {"label": "Save as my profile", "value": "Save as my profile"}


def _fields() -> dict[str, dict]:
    return {f["key"]: f for f in db.fields()}


def plain(key: str) -> str:
    """The plain factor name shown to users ("Water supply", "Cheap land")."""
    return _fields()[key]["plain_label"]


def plain_lc(key: str) -> str:
    """Plain name inside a sentence: first letter lower-cased only ("industrial areas (MIDC)")."""
    p = plain(key)
    return p[:1].lower() + p[1:]


# Plain names that are judgements read badly next to a value ("cheap land 0.10 (expensive)").
VALUE_NAMES = {"land": "land"}


def valued(key: str, display: str) -> str:
    """A factor with its value inside a sentence: "farm produce 5.2 M t", "land 0.10 (expensive)"."""
    return f"{VALUE_NAMES.get(key, plain_lc(key))} {display}"


def short(key: str) -> str:
    """Short technical name; used in chip *values*, which the modifier parser reads back."""
    return _fields()[key]["short"]


def chips(labels: list[str], values: list[str] | None = None) -> list[dict]:
    return [{"label": lab, "value": v} for lab, v in zip(labels, values or labels)]


def apply_chip(pid: str) -> dict:
    name = profile_name(pid)
    return {"label": f"Apply {name}", "value": f"Apply {name}"}


def preset_menu() -> list[dict]:
    return [apply_chip(p["id"]) | {"label": p["name"]} for p in db.profiles() if p["kind"] == "preset"]


def profile_name(pid: str) -> str:
    from .scoring import load_profile
    p = load_profile(pid)
    return p["name"] if p else pid


def _eligible(ranking: list[dict]) -> list[dict]:
    return sorted((e for e in ranking if e["eligible"]), key=lambda e: e["rank"])


def excluded_note(excluded: list[dict]) -> str:
    if not excluded:
        return ""
    kinds = []
    for x in excluded:
        for part in (x["reason"] or "").split("; "):
            k = part.split(" (")[0]
            if k and k not in kinds:
                kinds.append(k)
    n = len(excluded)
    return f" {n} district{'s' if n != 1 else ''} excluded ({'; '.join(kinds)})."


def top_line(profile: str, ranking: list[dict], n: int = 3) -> str:
    top = _eligible(ranking)[:n]
    if not top:
        return f"No district is eligible for **{profile}** under the current constraints."
    parts = [f"**{top[0]['name']}** ({top[0]['score']:.0f})"] + [f"{e['name']} ({e['score']:.0f})" for e in top[1:]]
    return f"Top districts for **{profile}**: {', '.join(parts)}."


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def modifier_note(changes: dict[str, str], before: dict[str, float] | None, after: dict[str, float]) -> str:
    """'**Water supply** now matters most.' In words; a factor that already had no weight says so."""
    bits = []
    for f, lab in changes.items():
        if after[f] == 0 and lab in ("low", "exclude") and (before is None or before[f] == 0):
            bits.append(f"**{plain(f)}** already doesn't count for this industry")
        else:
            bits.append(f"**{plain(f)}** {CHANGE_TEXT[lab]}")
    return "; ".join(bits) + "." if bits else ""


def rank(profile: str, ranking: list[dict], excluded: list[dict], changes: dict[str, str], weights: dict,
         switched_from: str | None = None) -> str:
    lines = []
    if switched_from:
        lines.append(f"Switched from {switched_from} to **{profile}**.")
    if changes:
        lines.append(modifier_note(changes, None, weights))
    lines.append(top_line(profile, ranking) + excluded_note(excluded))
    return " ".join(lines)


def suggest_preset(profile: str, changes: dict[str, str], current: str | None) -> str:
    """'This sounds like **Pharmaceuticals**.' Nothing is switched until the Apply chip is clicked."""
    s = f"This sounds like **{profile}**."
    if changes:
        s += " I'll also make " + _join([f"{plain_lc(f)} {PENDING_TEXT[lab]}" for f, lab in changes.items()]) + "."
    if current:
        s += f" The map stays on {current} until you apply it."
    return s


def suggest_general(changes: dict[str, str]) -> str:
    """Priorities but no industry yet (§7.2 rule 7b): offer General Manufacturing, never apply silently."""
    what = _join([f"{plain_lc(f)} {PENDING_TEXT[lab]}" for f, lab in changes.items()])
    return (f"Got it: {what}. Which industry is this for? I can start from **General Manufacturing**, "
            "or you can pick an industry.")


def already_on(profile: str, ranking: list[dict]) -> str:
    return f"You're already looking at **{profile}**. " + top_line(profile, ranking)


def pick_preset() -> str:
    return "Pick an industry:"


def adjust(changes: dict[str, str], before_w: dict, after_w: dict, prev_leader: dict | None, leader: dict | None) -> str:
    s = modifier_note(changes, before_w, after_w)
    if leader is None:
        return s + " No district is eligible under the current constraints."
    if prev_leader and prev_leader["district_id"] != leader["district_id"]:
        return f"{s} **{leader['name']}** now leads (was {prev_leader['name']})."
    return f"{s} **{leader['name']}** still leads ({leader['score']:.0f})."


def reset(profile: str, leader: dict | None) -> str:
    lead = f" **{leader['name']}** leads ({leader['score']:.0f})." if leader else ""
    return f"Back to the **{profile}** priorities.{lead}"


def ask_save_name() -> str:
    return "What should I call it?"


def saved(name: str) -> str:
    return f"Saved as **{name}**. You'll find it under My profiles."


def save_needs_profile() -> str:
    return "There's nothing to save yet. Pick an industry first, then adjust it and save."


def _field_rank(district_id: int, key: str, eligible_ids: list[int]) -> tuple[int, int]:
    d = dataset()
    j = d.keys.index(key)
    vals = sorted(((d.norm[d.row(i), j], i) for i in eligible_ids), key=lambda x: -x[0])
    me = d.norm[d.row(district_id), j]
    better = sum(1 for v, _ in vals if v > me + 1e-12)
    return better + 1, len(vals)


def focus_ranks(district_id: int, ranking: list[dict], weights: dict, field_focus: list[str]) -> list[dict]:
    """The factors the user asked about: the district's value and its place among eligible districts."""
    values = {v["field"]: v for v in facts.district_values(district_id, weights)}
    ids = [e["district_id"] for e in _eligible(ranking)]
    out = []
    for key in field_focus:
        r, n = _field_rank(district_id, key, ids)
        out.append({"field": key, "display": values[key]["display"], "rank": r, "of": n})
    return out


def explain(district_id: int, ranking: list[dict], weights: dict, profile: str, field_focus: list[str]) -> str:
    entry = next(e for e in ranking if e["district_id"] == district_id)
    eligible = _eligible(ranking)
    name = entry["name"]
    if not entry["eligible"]:
        head = f"**{name}** is not ranked for **{profile}**: {entry['reason']}."
    else:
        lo, hi = entry["band"]
        band = f"likely {lo}–{hi}" if lo != hi else f"stable at {lo}"
        head = (f"**{name}** ranks #{entry['rank']} of {len(eligible)} for **{profile}** "
                f"({band}, confidence {entry['confidence']}), score {entry['score']:.1f}.")
    lines = [head]
    for f in focus_ranks(district_id, ranking, weights, field_focus):
        lines.append(f"{plain(f['field'])}: {f['display']} (#{f['rank']} of {f['of']} eligible districts on this factor).")
    st = facts.strengths(district_id, weights)
    wk = facts.weaknesses(district_id, weights)
    lines.append("Strengths: " + ", ".join(valued(s["field"], s["display"]) for s in st) + "."
                 if st else facts.NO_STRENGTHS + ".")
    if wk:
        lines.append("Weaknesses: " + ", ".join(valued(w["field"], w["display"]) for w in wk) + ".")
    if entry["eligible"]:
        if entry["rank"] == 1 and len(eligible) > 1:
            lines.append(f"Lead over #2 {eligible[1]['name']}: {entry['score'] - eligible[1]['score']:.1f} pts.")
        elif entry["rank"] != 1:
            wnf = facts.why_not_first(district_id, ranking, weights)
            behind = ", ".join(plain_lc(g["field"]) for g in wnf["behind"])
            lines.append(f"Gap to #1 {wnf['first']['name']}: {wnf['total_gap']:.1f} pts"
                         + (f" (mostly {behind})." if behind else "."))
    return " ".join(lines)


def compare(cmp: dict, profile: str) -> str:
    a, b = cmp["a"], cmp["b"]

    def tag(x: dict) -> str:
        return f"#{x['rank']}" if x["eligible"] else "not ranked"

    head = f"**{a['name']}** {a['score']:.1f} ({tag(a)}) vs **{b['name']}** {b['score']:.1f} ({tag(b)}) for **{profile}**"
    if cmp["direction"] == "tie":
        head += ": level on score."
    else:
        win = a if cmp["direction"] == "a" else b
        head += f": {win['name']} leads by {abs(cmp['score_diff']):.1f} pts."
    weighted = [r for r in cmp["rows"] if r["weight"] >= 0.05 and r["leader"] != "tie"]
    weighted.sort(key=lambda r: -abs(r["a"]["points"] - r["b"]["points"]))
    a_lead = [plain_lc(r["field"]) for r in weighted if r["leader"] == "a"][:3]
    b_lead = [plain_lc(r["field"]) for r in weighted if r["leader"] == "b"][:3]
    parts = []
    if a_lead:
        parts.append(f"{a['name']} is ahead on {', '.join(a_lead)}")
    if b_lead:
        parts.append(f"{b['name']} on {', '.join(b_lead)}")
    return head + (" " + "; ".join(parts) + "." if parts else "")


def clarify_ambiguous(term: str, names: list[str]) -> str:
    return f"**{term}** fits more than one industry. Did you mean: " + " or ".join(f"**{n}**" for n in names) + "?"


def clarify_unsupported(term: str, resource: str, names: list[str]) -> str:
    return (f"I don't have the data to rank {term} reliably (it depends on {resource}). "
            f"Closest supported industries: {', '.join(names)}.")


def clarify_modifier(names: list[str]) -> str:
    return (f"I'm not sure whether {' and '.join(names)} should count for more or less. "
            "Nothing was changed; pick one:")


def clarify_out_of_scope(term: str) -> str:
    return (f"GrowthCompass ranks districts for industrial investment, so it can't rank a {term}. "
            "Pick an industry to rank:")


def clarify_unknown(names: list[str], have_embeddings: bool) -> str:
    if have_embeddings:
        return "I couldn't tell which industry you mean. Did you mean: " + ", ".join(names) + "?"
    return "I couldn't tell which industry you mean. Pick an industry:"


def help_text(has_ranking: bool) -> str:
    base = ("I rank Maharashtra's 36 districts for an industry. Tell me what you want to build "
            "(\"a dairy plant\"), what matters (\"water is critical\", \"ignore land cost\"), or ask to "
            "compare or explain districts (\"compare Pune and Nashik\", \"why is Satara ranked 4?\").")
    return base if not has_ranking else base + " Say \"reset\" to go back to the industry's priorities."


def chat(profile: str, ranking: list[dict], weights: dict) -> str:
    top = top_line(profile, ranking)
    heavy = [k for k, v in sorted(weights.items(), key=lambda kv: -kv[1]) if v > 0][:3]
    return f"{top} What matters most here: {_join([plain_lc(k) for k in heavy])}."


def ask_reply(profile: str, ranking: list[dict], weights: dict, needs: list[str]) -> str:
    """§19.1 instant answer to a question (the AI note follows): the ranking in brief and what the data
    does not cover for this industry."""
    s = chat(profile, ranking, weights)
    if needs:
        s += " Also check (not in our data): " + "; ".join(n[:1].lower() + n[1:] for n in needs[:5]) + "."
    return s


def what_if(changes: dict[str, str], p: dict) -> str:
    """§19.2 'What if land mattered more?' as a preview; nothing changes until Apply."""
    past = {"critical": "mattered most", "high": "mattered more", "low": "mattered less", "exclude": "were ignored"}
    what = _join([f"**{plain_lc(f)}** {past[lab]}" for f, lab in changes.items()])
    if p["leader_after"] is None:
        tail = "no district would be eligible."
    elif p["leader_after"] != p["leader_before"]:
        tail = f"**{p['leader_after']}** would move to #1 (now {p['leader_before']})."
    elif p["after"] != p["before"]:
        tail = f"**{p['leader_after']}** would stay #1; the top 3 would become {_join(p['after'])}."
    else:
        tail = f"the top 3 would stay the same ({_join(p['after'])})."
    return f"If {what}: {tail} Nothing has changed yet."


def unparsed_note(fields_: list[str]) -> str:
    names = _join([plain_lc(f) for f in fields_])
    return f" You mentioned {names} without saying how much it matters."
