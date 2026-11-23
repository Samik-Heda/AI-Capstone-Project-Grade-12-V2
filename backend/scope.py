"""Out-of-scope guard (spec §8.2 refusal, §14.4). Deterministic, runs before any state change.

GrowthCompass answers only from its district dataset. Questions about other places, forecasts,
people, politics, weather, personal finance or creative writing get the fixed refusal sentence
instead of being misread as priorities ("will land prices rise?" must not raise the land weight).
Rules match whole tokens or token phrases; the dev set is eval/scope_dev.jsonl, the blind set
eval/scope_holdout.jsonl. With Ollama running, the chat fact sheet repeats the same rule as a backstop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import nlp

# Places outside Maharashtra: other states/UTs, big non-Maharashtra cities, countries.
OTHER_PLACES = {
    "gujarat", "karnataka", "kerala", "goa", "telangana", "andhra", "tamil", "tamilnadu", "rajasthan", "punjab",
    "haryana", "bihar", "odisha", "orissa", "bengal", "assam", "jharkhand", "chhattisgarh", "uttarakhand",
    "himachal", "kashmir", "delhi", "bangalore", "bengaluru", "hyderabad", "chennai", "kolkata", "calcutta",
    "ahmedabad", "surat", "vadodara", "indore", "bhopal", "lucknow", "jaipur", "noida", "gurgaon", "gurugram",
    "mysore", "mysuru", "coimbatore", "kochi", "visakhapatnam", "china", "usa", "america", "germany", "japan",
    "vietnam", "bangladesh", "pakistan", "dubai", "singapore", "europe", "africa", "uk", "england",
}
OTHER_PLACE_PHRASES = [("madhya", "pradesh"), ("uttar", "pradesh"), ("tamil", "nadu"), ("west", "bengal"),
                       ("which", "state"), ("which", "country"), ("other", "state"), ("other", "states")]

# Subjects the dataset has no data on (single tokens, matched before lemmatization).
TOPICS = {
    # weather and geography
    "weather", "temperature", "climate", "monsoon", "elevation", "altitude",
    # economy and finance outside the 13 factors
    "gdp", "income", "unemployment", "inflation", "gst", "tax", "taxes", "stock", "stocks", "shares", "sensex",
    "nifty", "gold", "rupee", "dollar", "loan", "loans", "savings", "salary", "salaries", "wages", "crypto",
    "bitcoin", "mutual", "subsidy", "subsidies",
    # politics and people
    "election", "elections", "minister", "mla", "mlas", "mp", "politics", "political", "politician", "governor",
    "collector", "mayor", "richest", "billionaire", "founder", "founded", "ceo",
    # society, culture, leisure
    "crime", "tourist", "tourists", "tourism", "history", "historical", "fort", "temple", "festival", "recipe",
    "recipes", "cricket", "football", "movie", "movies", "film", "restaurant", "restaurants", "news",
    # property market (land affordability is the only land factor)
    "property", "apartment", "flat", "flats", "rent", "housing",
}
TOPIC_PHRASES = [("per", "capita"), ("exchange", "rate"), ("interest", "rate"), ("interest", "rates"),
                 ("stock", "market"), ("real", "estate"), ("how", "hot"), ("capital", "of"),
                 ("which", "party"), ("ruling", "party"), ("party", "rule"), ("party", "rules"),
                 ("population", "growth")]

# Creative or general-assistant requests.
CREATIVE = {"poem", "poems", "song", "songs", "story", "stories", "joke", "jokes", "essay", "haiku", "limerick",
            "rap", "lyrics", "translate", "translation"}
SERVICE_VERBS = {"book", "reserve", "recommend", "order", "buy", "sell"}
SERVICE_NOUNS = {"hotel", "hotels", "ticket", "tickets", "train", "flight", "flights", "taxi", "cab", "restaurant",
                 "table", "room"}
HOWTO_PHRASES = [("how", "do", "i", "register"), ("how", "to", "register"), ("how", "do", "i", "get"),
                 ("how", "can", "i", "get")]

# Forecasts: the data is a snapshot, so nothing about the future can be answered.
FORECAST = {"forecast", "forecasts", "predict", "prediction", "predictions", "projection", "projections",
            "tomorrow"}
CHANGE_VERBS = {"rise", "rises", "fall", "falls", "grow", "grows", "increase", "increases", "decrease", "decreases",
                "drop", "drops", "crash", "boom", "climb", "decline"}
CHANGE_PHRASES = [("go", "up"), ("go", "down"), ("going", "up"), ("going", "down")]
FUTURE_MARKERS = [("next", "year"), ("next", "years"), ("next", "decade"), ("next", "month"), ("coming", "years")]
YEAR_RE = re.compile(r"^20[2-9]\d$")
NEXT_N_RE = re.compile(r"\bnext \d+ (year|years|month|months)\b")

# A "who ..." question is about a person unless it is clearly about ranking.
RANK_WORDS = {"rank", "ranks", "ranked", "first", "top", "lead", "leads", "best", "winner", "second", "last"}
NOT_PARTY = {"third"}  # "third party logistics" is a business model, not politics


@dataclass(frozen=True)
class Verdict:
    out_of_scope: bool
    reason: str | None = None   # place | topic | creative | service | forecast | person
    cue: str | None = None


def _has_phrase(tokens: list[str], phrases: list[tuple[str, ...]]) -> tuple[str, ...] | None:
    for p in phrases:
        n = len(p)
        for i in range(len(tokens) - n + 1):
            if tuple(tokens[i:i + n]) == p:
                if p[0] == "party" and i > 0 and tokens[i - 1] in NOT_PARTY:
                    continue
                return p
    return None


def check(text: str) -> Verdict:
    toks = [t for t in nlp.tokenize(text) if t not in nlp.SPLIT_PUNCT and t not in {"?", "!"}]
    ts = set(toks)
    norm = " ".join(toks)

    hit = ts & OTHER_PLACES
    if hit:
        return Verdict(True, "place", sorted(hit)[0])
    p = _has_phrase(toks, OTHER_PLACE_PHRASES)
    if p:
        return Verdict(True, "place", " ".join(p))

    hit = ts & CREATIVE
    if hit:
        return Verdict(True, "creative", sorted(hit)[0])
    if ts & SERVICE_VERBS and ts & SERVICE_NOUNS:
        return Verdict(True, "service", sorted(ts & SERVICE_NOUNS)[0])
    p = _has_phrase(toks, HOWTO_PHRASES)
    if p:
        return Verdict(True, "service", " ".join(p))

    hit = ts & FORECAST
    if hit:
        return Verdict(True, "forecast", sorted(hit)[0])
    future = bool(_has_phrase(toks, FUTURE_MARKERS) or NEXT_N_RE.search(norm) or any(YEAR_RE.match(t) for t in toks))
    change = bool(ts & CHANGE_VERBS or _has_phrase(toks, CHANGE_PHRASES))
    # "will prices rise", "next year", "in 2040" with will/change; a plan ("a plant in 2027") is not a forecast
    if change and ("will" in ts or future) or future and "will" in ts:
        return Verdict(True, "forecast", "future")

    hit = ts & TOPICS
    if hit:
        return Verdict(True, "topic", sorted(hit)[0])
    p = _has_phrase(toks, TOPIC_PHRASES)
    if p:
        return Verdict(True, "topic", " ".join(p))

    if toks and toks[0] == "who" and not ts & RANK_WORDS:
        return Verdict(True, "person", "who")
    return Verdict(False)
