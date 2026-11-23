"""Shared text handling for the language layer (spec §7): normalization, tokenization, lemmatization
and a longest-phrase-first matcher over whole tokens. Nothing here ever matches substrings."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from . import config

_APOS = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'"})
_NEG_CONTRACTIONS = [
    (r"\bcan't\b", "can not"), (r"\bwon't\b", "will not"), (r"\bshan't\b", "shall not"),
    (r"n't\b", " not"),
    (r"\bdont\b", "do not"), (r"\bdoesnt\b", "does not"), (r"\bisnt\b", "is not"), (r"\barent\b", "are not"),
    (r"\bdidnt\b", "did not"), (r"\bcant\b", "can not"), (r"\bwont\b", "will not"),
]
_TOKEN = re.compile(r"[a-z0-9]+|[,;.!?]")
SPLIT_PUNCT = {",", ";", "."}


def normalize(text: str) -> str:
    """Lowercase, unify apostrophes, expand negative contractions, collapse whitespace."""
    t = text.translate(_APOS).lower()
    for pat, rep in _NEG_CONTRACTIONS:
        t = re.sub(pat, rep, t)
    return re.sub(r"\s+", " ", t).strip()


def tokenize(text: str) -> list[str]:
    """Word tokens plus clause punctuation. 'two-wheeler' → two, wheeler; "nashik's" → nashik, s."""
    return _TOKEN.findall(normalize(text))


# Plurals whose meaning differs from the singular ("goods transport" is not "good transport").
LEMMA_KEEP = {"goods", "glasses", "spirits", "works", "customs", "arms", "means", "news", "sales", "savings",
              "trucking", "trained", "training"}  # haulage is not "truck"; "trained staff" is not "train"


def lemma(tok: str) -> str:
    """Light stemming (§7.3), applied identically to lexicon terms and user text:
    -ing / -ed (keeping a ≥4-letter stem, so "caring" never becomes "car"), -ies→y, -es after
    sibilants/o, plural -s, then a trailing -e, so bottle / bottles / bottled / bottling all meet."""
    if tok in LEMMA_KEEP:
        return tok
    t = tok
    # plural first, so "castings" and "casting" reach the same stem
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 3 and t.endswith("es") and re.search(r"(x|z|ch|sh|ss|o)es$", t):
        t = t[:-2]
    elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    if len(t) >= 7 and t.endswith("ing"):
        t = t[:-3]
    elif len(t) >= 6 and t.endswith("ed"):
        t = t[:-2]
    if len(t) >= 5 and t.endswith("e"):
        t = t[:-1]
    return t


def phrase_key(phrase: str) -> tuple[str, ...]:
    return tuple(lemma(t) for t in tokenize(phrase) if t not in SPLIT_PUNCT)


@dataclass(frozen=True)
class Match:
    start: int          # token index (inclusive)
    end: int            # token index (exclusive)
    kind: str           # 'industry' | 'unsupported' | 'generic' | 'field' | 'cue'
    values: tuple       # profile ids / field key / (label,) / (resource,)
    text: str           # the matched phrase as written in the lexicon


class PhraseMatcher:
    """Longest-match-first over whole tokens. At each length the exact surface form wins over the
    stemmed form, so a listed "trucking" (logistics) beats the stem of "truck" (automobile).
    With stem=False only exact surface forms match (district names: "Thane" must not match "than")."""

    def __init__(self, stem: bool = True) -> None:
        self.stem = stem
        self.exact: dict[tuple[str, ...], tuple[str, tuple, str]] = {}
        self.stemmed: dict[tuple[str, ...], tuple[str, tuple, str]] = {}
        self.maxlen = 1

    @staticmethod
    def _put(table: dict, key: tuple, kind: str, value, phrase: str) -> None:
        if key in table:
            k0, v0, t0 = table[key]
            if tokenize(t0) != tokenize(phrase):
                # Two different words share a stem ("truck" / "trucking"): the one closer to the
                # stem keeps it; the other still matches by its exact surface form.
                if len(phrase) < len(t0):
                    table[key] = (kind, (value,), phrase)
                return
            if k0 == kind and value not in v0:  # same phrase under two profiles → ambiguous
                table[key] = (k0, v0 + (value,), t0)
            return
        table[key] = (kind, (value,), phrase)

    def add(self, phrase: str, kind: str, value) -> None:
        surface = tuple(t for t in tokenize(phrase) if t not in SPLIT_PUNCT)
        if not surface:
            return
        self._put(self.exact, surface, kind, value, phrase)
        if self.stem:
            self._put(self.stemmed, tuple(lemma(t) for t in surface), kind, value, phrase)
        self.maxlen = max(self.maxlen, len(surface))

    def find(self, tokens: list[str], blocked: set[int] | None = None) -> list[Match]:
        """`tokens` are surface tokens from tokenize(); stemming happens here."""
        blocked = blocked or set()
        lem = [lemma(t) for t in tokens] if self.stem else tokens
        out, i, n = [], 0, len(tokens)
        while i < n:
            hit = None
            for L in range(min(self.maxlen, n - i), 0, -1):
                if any(j in blocked for j in range(i, i + L)):
                    continue
                entry = self.exact.get(tuple(tokens[i:i + L])) or (
                    self.stemmed.get(tuple(lem[i:i + L])) if self.stem else None)
                if entry:
                    hit = Match(i, i + L, entry[0], entry[1], entry[2])
                    break
            if hit:
                out.append(hit)
                i = hit.end
            else:
                i += 1
        return out


# ---------- lexicon files ----------

@lru_cache(maxsize=1)
def industry_lexicon() -> dict:
    return json.loads((config.DATA_DIR / "lexicon_industry.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def field_lexicon() -> dict:
    return json.loads((config.DATA_DIR / "lexicon_fields.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def field_synonyms() -> dict[str, list[str]]:
    from . import db
    extra = field_lexicon()["extra_synonyms"]
    return {f["key"]: list(dict.fromkeys(f.get("synonyms", []) + extra.get(f["key"], []))) for f in db.fields()}


@lru_cache(maxsize=1)
def term_matcher() -> PhraseMatcher:
    """One matcher for industry terms, unsupported terms, generic terms and field synonyms, so the
    longest phrase wins across all of them ('mineral water' is beverages, not the water field;
    'small businesses' is MSME, not a generic 'business')."""
    m = PhraseMatcher()
    lex = industry_lexicon()
    for term, resource in lex["unsupported"].items():
        m.add(term, "unsupported", resource)
    for term in lex.get("out_of_scope", []):
        m.add(term, "out_of_scope", term)
    for pid, terms in lex["profiles"].items():
        for t in terms:
            m.add(t, "industry", pid)
    for field, syns in field_synonyms().items():
        for s in syns:
            m.add(s, "field", field)
    for stop in field_lexicon()["stoplist"]:
        m.add(stop, "stop", None)
    for g in lex["generic"]:
        m.add(g, "generic", "generic")
    return m


@lru_cache(maxsize=1)
def cue_matcher() -> PhraseMatcher:
    m = PhraseMatcher()
    phrases = [(p, label) for label, ps in field_lexicon()["intensity"].items() for p in ps]
    for p, label in sorted(phrases, key=lambda x: -len(phrase_key(x[0]))):
        m.add(p, "cue", label)
    return m


# Common words that carry no product meaning. Used to decide whether a generic-only match
# ("factory") has a product noun next to it, and to keep fuzzy matching off ordinary words.
STOPWORDS = set("""
a an the i we me my our us you your it its this that these those there here he she they them their
is are was were be been being am do does did done have has had having will would shall should can could
may might must to of in on at by for with from into onto about over under near around between within
and or but if then so than as also too very just only even still yet not no nor
want wants wanted need needs like looking look plan planning planned set setting up start starting
build building open opening new big small large medium little some any all each every few more most
best good great top better nice right well where which what who whom whose when why how much many
district districts place places location locations site sites area areas region regions state city
maharashtra india show tell give find suggest recommend rank ranking ranked please thanks thank ok okay
one two three first second now then again also lot lots kind type sort something anything thing things
make making made run running put own based want would like go going get getting let help
packing product products goods items material materials service services office
across through via using per day daily us anything everything nothing village villages people based
""".split())


def residual_content(tokens: list[str], covered: set[int]) -> list[str]:
    """Tokens that are not covered by a match, not stopwords, not punctuation and not numbers."""
    return [t for i, t in enumerate(tokens)
            if i not in covered and t not in SPLIT_PUNCT and t not in STOPWORDS and not t.isdigit() and len(t) > 1]
