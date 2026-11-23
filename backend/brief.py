"""Business brief (spec §18.3): a printable one-page site summary for the session's current priorities.

Most of it is deterministic and built from the facts layer: priorities, what the user told the advisor,
the top 3 with strengths and weaknesses, risks, next steps and data sources. Four short paragraphs are
written by the LLM, section by section (the quality model on AC when installed). Each paragraph goes
through narrate.check; one that fails is replaced by its templated text, so the brief never prints an
unchecked number."""
from __future__ import annotations

import hashlib
import json
from contextlib import aclosing
from typing import AsyncIterator

from . import config, db, facts, narrate, power, templates
from .narrate import Sheet, _compose, _f, _pair, _short, check, explain_sheet, sse

TOP_N = 3
# What to check on the ground when a district is weak on a factor (plain words, no figures).
NEXT_STEP = {
    "literacy": "Talk to the local ITI and colleges in {d} about hiring trained workers.",
    "road_density": "Drive the last-mile roads around {d} before choosing a plot.",
    "highway": "Check the travel time from {d} to the nearest national highway.",
    "rail": "Ask about the nearest railway goods shed if you move heavy loads from {d}.",
    "port": "Work out the trucking cost from {d} to JNPA (Nhava Sheva) port if you export.",
    "power": "Ask MSEDCL about supply hours and the cost of an industrial connection in {d}.",
    "water": "Ask MIDC or the irrigation department about water allocation for industry in {d}.",
    "crop_output": "Line up farm-produce suppliers near {d} before you invest.",
    "crop_diversity": "Check which crops are available near {d} in each season.",
    "irrigation": "Check how much farmland near {d} is irrigated, and in which seasons.",
    "estates": "Visit the MIDC regional office for {d} and ask which industrial plots are free.",
    "msme": "Look for small suppliers and job-work units near {d}.",
    "land": "Compare plot prices in {d} with a local broker and the MIDC rate card.",
}
GENERAL_STEPS = [
    "Visit the shortlisted districts yourself and meet the District Industries Centre (DIC).",
    "Ask about Maharashtra state incentives (Package Scheme of Incentives) for your industry and taluka.",
    "Verify the estimated figures in this brief locally before committing money.",
]


def cfg() -> dict:
    return config.load().get("brief", {})


def _eligible(ranking: list[dict]) -> list[dict]:
    return sorted((e for e in ranking if e["eligible"]), key=lambda e: e["rank"])


def state_key(s) -> str:
    raw = json.dumps([s.profile_id, s.weights, s.constraints, s.business_notes], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _level(rank: int) -> str:
    return "matters most" if rank == 0 else ("matters a lot" if rank < 3 else "matters")


def build(s) -> dict:
    """The deterministic part of the brief, plus the LLM sections to write and their fallbacks."""
    elig = _eligible(s.ranking)
    top = elig[:TOP_N]
    fields = {f["key"]: f for f in db.fields()}
    heavy = [k for k, w in sorted(s.weights.items(), key=lambda kv: -kv[1]) if w >= facts.MIN_WEIGHT][:6]
    priorities = [{"field": k, "label": templates.plain(k), "level": _level(i)} for i, k in enumerate(heavy)]
    changes = [{"field": f, "label": templates.plain(f), "change": templates.CHANGE_TEXT[lab]}
               for f, lab in s.modifiers.items()]
    districts = []
    for e in top:
        did = e["district_id"]
        st, wk = facts.strengths(did, s.weights), facts.weaknesses(did, s.weights)
        districts.append({
            "district_id": did, "name": e["name"], "region": e["region"], "rank": e["rank"],
            "score": e["score"], "band": e["band"], "confidence": e["confidence"],
            "strengths": [{"field": x["field"], "label": templates.plain(x["field"]), "display": x["display"]} for x in st],
            "weaknesses": [{"field": x["field"], "label": templates.plain(x["field"]), "display": x["display"]} for x in wk],
        })
    risks, steps = [], []
    for d in districts:
        for w in d["weaknesses"][:2]:
            risks.append(f"{d['name']}: weak on {w['label'].lower()} ({w['display']}).")
            steps.append(NEXT_STEP[w["field"]].format(d=d["name"]))
    estimated = [templates.plain(k) for k in heavy if fields[k]["status"] != "Sourced"]
    if estimated:
        risks.append("These figures are estimates, not official statistics: " + ", ".join(estimated).lower() + ".")
    lead = districts[0]["name"] if districts else None
    if lead and "estates" in heavy and not any(s_.startswith("Visit the MIDC") for s_ in steps):
        steps.insert(0, NEXT_STEP["estates"].format(d=lead))
    steps = list(dict.fromkeys(steps)) + GENERAL_STEPS
    sources = [{"label": templates.plain(k), "source": fields[k]["source"], "status": fields[k]["status"]} for k in heavy]
    about = json.loads((config.DATA_DIR / "about.json").read_text(encoding="utf-8"))
    sections = [{"id": "summary", "title": "Summary", "fallback": summary_fallback(s, districts, priorities)}]
    for d in districts:
        sections.append({"id": f"why_{d['district_id']}", "title": f"Why {d['name']}",
                         "district_id": d["district_id"],
                         "fallback": templates.explain(d["district_id"], s.ranking, s.weights, s.profile_name, [])})
    return {
        "key": state_key(s), "profile": s.profile_name, "eligible": len(elig), "excluded": len(s.excluded),
        "priorities": priorities, "changes": changes, "notes": list(s.business_notes.values()),
        "districts": districts, "risks": risks, "next_steps": steps, "sources": sources,
        "also_check": db.industry_needs(s.profile_id),  # §19.4 curated, not district data
        "status_note": db.registry()["status_note"], "limitations": about["limitations"],
        "dataset_hash": db.meta_value("dataset_hash"), "sections": sections,
    }


def summary_fallback(s, districts: list[dict], priorities: list[dict]) -> str:
    if not districts:
        return f"No district is eligible for {s.profile_name} under the current constraints."
    top = ", ".join(f"{d['name']} ({d['score']:.0f})" for d in districts)
    pri = templates._join([p["label"].lower() for p in priorities[:3]])
    return (f"For {s.profile_name}, the best-placed districts are {top}. "
            f"The ranking leans most on {pri}.")


def summary_sheet(s, b: dict) -> Sheet | None:
    ds = b["districts"]
    if not ds:
        return None
    lines = [f"{b['profile']}: {b['eligible']} districts ranked. Top 3: "
             + ", ".join(f"{d['rank']} {d['name']} {_f(d['score'])}" for d in ds) + "."]
    lines.append("Matters most: " + ", ".join(_short(p["field"]) for p in b["priorities"][:3]) + ".")
    if b["notes"]:
        lines.append("The owner said: " + "; ".join(n.lower() for n in b["notes"][:4]) + ".")
    st = ds[0]["strengths"][:2]
    if st:
        lines.append(f"{ds[0]['name']} strengths: " + ", ".join(_short(x["field"]) for x in st) + ".")
    pair = None
    if len(ds) > 1:
        cmp = facts.compare(ds[0]["district_id"], ds[1]["district_id"], s.ranking, s.weights)
        pair = _pair(ds[0]["district_id"], ds[1]["district_id"], cmp)
    task = ("Write a 3-sentence summary for a bank manager: which district fits best and why. "
            "Name only the districts and factors listed.")
    return Sheet("brief_summary", _compose(lines, task), {d["district_id"] for d in ds}, pair)


def section_sheet(s, b: dict, sec: dict) -> Sheet | None:
    if sec["id"] == "summary":
        return summary_sheet(s, b)
    return explain_sheet(sec["district_id"], s.ranking, s.weights, s.profile_name, [])


def pick_brief_model(narrator, status) -> str | None:
    """7B on AC when installed and enabled; otherwise the narration model order (1.5B first on battery)."""
    if cfg().get("use_quality_model", True) and not power.battery_mode() \
            and status._present(narrator.quality_model, status.models):
        return narrator.quality_model
    return narrate.pick_model(narrator, status)


def limits() -> tuple[float, float]:
    c = cfg()
    total = c.get("battery_section_timeout_s", 240) if power.battery_mode() else c.get("section_timeout_s", 150)
    return float(c.get("first_token_timeout_s", 90)), float(total)


async def events(narrator, status, s) -> AsyncIterator[str]:
    """SSE body for /api/brief: `brief` {deterministic data} → per section `section` {id}, `token` {id, t},
    `section_done` {id, text, source: ai|template, grounding} → `done` {model, cached}."""
    if not s.profile_id:
        yield sse("error", {"message": "Pick an industry first, then make the brief."})
        return
    b = build(s)
    cache = s.brief_cache if s.brief_cache and s.brief_cache.get("key") == b["key"] else None
    yield sse("brief", {k: v for k, v in b.items() if k != "sections"}
              | {"sections": [{"id": x["id"], "title": x["title"]} for x in b["sections"]]})
    if cache and len(cache["sections"]) == len(b["sections"]):
        for sec in b["sections"]:
            yield sse("section_done", cache["sections"][sec["id"]])
        yield sse("done", {"model": cache.get("model"), "cached": True})
        return
    narr_on = config.load()["features"].get("narration", True)
    model = pick_brief_model(narrator, status) if (status.reachable and narr_on) else None
    done: dict[str, dict] = {}
    for sec in b["sections"]:
        sheet = section_sheet(s, b, sec) if model else None
        text, grounding, source = "", None, "template"
        if sheet is not None:
            yield sse("section", {"id": sec["id"], "model": model})
            async with aclosing(narrator.stream(sheet.text, model, num_predict=int(cfg().get("num_predict", 140)),
                                                limits=limits())) as evs:
                async for ev, data in evs:
                    if ev == "token":
                        yield sse("token", {"id": sec["id"], "t": data["t"]})
                    elif ev == "done":
                        text = data["text"].strip()
                    elif ev == "error":
                        text = ""
            if text:
                grounding = check(text, sheet)
                source = "ai" if grounding["grounded"] else "template"
        out = {"id": sec["id"], "text": text if source == "ai" else sec["fallback"], "source": source,
               "grounding": grounding, "ai_text": text if source != "ai" and text else None}
        done[sec["id"]] = out
        yield sse("section_done", out)
    s.brief_cache = {"key": b["key"], "sections": done, "model": model}
    yield sse("done", {"model": model, "cached": False})
