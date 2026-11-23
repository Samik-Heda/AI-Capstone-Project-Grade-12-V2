"""Modifier parser (spec §7.4): field mentions + intensity cues + negation scope → {field: label}.

Labels and multipliers are §5.4. Everything is whole-token matching ("airport" never means port,
"important" never means port)."""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from . import nlp

MULTIPLIERS = {"exclude": 0.0, "low": 0.4, "high": 1.8, "critical": 3.0}
# The lexicon still groups "slightly less/more" phrases so a quantifier can size the cue after it
# ("slightly less focus"), but the output has only the four §5.4 labels (product-owner decision 2026-10-01).
FOLD = {"slight_down": "low", "slight_up": "high"}
ZERO_BASE_FLOOR = 0.05
NEGATION_WINDOW = 3
_FLIP = {"high": "low", "critical": "low", "slight_up": "low"}
AVERSION_CUES = {"avoid"}
_PRIORITY = ["exclude", "low", "critical", "high", "slight_up", "slight_down"]


@dataclass
class Parsed:
    modifiers: list[dict] = dc_field(default_factory=list)     # [{field, label, span}]
    unparsed_fields: list[str] = dc_field(default_factory=list)
    uncertain: list[str] = dc_field(default_factory=list)       # negated, but no cue we understand
    mentioned: list[str] = dc_field(default_factory=list)       # every field mentioned, in order

    def as_map(self) -> dict[str, str]:
        return {m["field"]: m["label"] for m in self.modifiers}


def _clause_ids(tokens: list[str]) -> list[int]:
    splitters = set(nlp.field_lexicon()["clause_splitters"])
    ids, cur = [], 0
    for t in tokens:
        if t in splitters:
            cur += 1
            ids.append(-1)  # the splitter itself belongs to no clause
        else:
            ids.append(cur)
    return ids


def _clause_has_negator(tokens: list[str], clause: list[int], f: nlp.Match) -> bool:
    negators = set(nlp.field_lexicon()["negators"])
    return any(clause[j] == clause[f.start] and tokens[j] in negators for j in range(len(tokens)))


def _negated_mention(tokens: list[str], clause: list[int], f: nlp.Match) -> bool:
    negators = set(nlp.field_lexicon()["negators"])
    return any(clause[j] == clause[f.start] and tokens[j] in negators
               for j in range(max(0, f.start - NEGATION_WINDOW), f.start))


def _elliptical_negation(tokens: list[str], clause: list[int], f: nlp.Match, cues: list[nlp.Match]) -> bool:
    """'<field> is not' / '<field> are not' / '<field> not' closing the clause, with no cue after it."""
    negators = set(nlp.field_lexicon()["negators"])
    cid = clause[f.start]
    for j in range(f.end, min(len(tokens), f.end + 3)):
        if clause[j] != cid:
            return False
        if tokens[j] in negators:
            rest = [k for k in range(j + 1, len(tokens)) if clause[k] == cid]
            return not rest or not any(c.start > j and clause[c.start] == cid for c in cues)
        if tokens[j] not in ("is", "are", "does", "do", "was", "will", "should"):
            return False
    return False


def _negated(tokens: list[str], clause: list[int], cue: nlp.Match) -> bool:
    negators = set(nlp.field_lexicon()["negators"])
    cid = clause[cue.start]
    for j in range(max(0, cue.start - NEGATION_WINDOW), cue.start):
        if clause[j] == cid and tokens[j] in negators:
            return True
    return False


def _find_cues(tokens: list[str], blocked: set[int]) -> list[nlp.Match]:
    """Cue matches with filler words skipped ("does not really matter" = "does not matter")."""
    fill = nlp.PhraseMatcher(stem=False)
    for f in nlp.field_lexicon().get("fillers", []):
        fill.add(f, "filler", None)
    skip = {j for m in fill.find(tokens) for j in range(m.start, m.end)}
    keep = [i for i in range(len(tokens)) if i not in skip]
    kept_blocked = {k for k, i in enumerate(keep) if i in blocked}
    out: list[nlp.Match] = []
    for m in nlp.cue_matcher().find([tokens[i] for i in keep], blocked=kept_blocked):
        m = nlp.Match(keep[m.start], keep[m.end - 1] + 1, m.kind, m.values, m.text)
        # A quantifier directly before another cue sets the size: "slightly less focus", "a bit more emphasis"
        if out and out[-1].values[0] in ("slight_down", "slight_up") and m.values[0] in ("high", "critical")                 and m.start - out[-1].end <= 1:
            q = out.pop()
            m = nlp.Match(q.start, m.end, "cue", q.values, f"{q.text} {m.text}")
        out.append(m)
    return out


def _aversive(tokens: list[str], clause: list[int], f: nlp.Match) -> bool:
    """The field is described as something to avoid ("expensive land", "far from the port")."""
    adj = nlp.field_lexicon().get("aversion_adjectives", {}).get(f.values[0], [])
    words = {a for a in adj if " " not in a}
    phrases = [tuple(a.split()) for a in adj if " " in a]
    lo = max(0, f.start - 4)
    window = [j for j in range(lo, f.end) if clause[j] == clause[f.start]]
    toks = [tokens[j] for j in window]
    return any(t in words for t in toks) or any(
        tuple(toks[i:i + len(ph)]) == ph for ph in phrases for i in range(len(toks)))


def parse(text: str, tokens: list[str] | None = None, term_matches: list[nlp.Match] | None = None,
          default_label: str | None = None) -> Parsed:
    """`default_label` is applied to fields mentioned without a cue (the router passes 'high' in an
    adjust context, so "focus on water" or plain "water" both mean water: high)."""
    tokens = tokens if tokens is not None else nlp.tokenize(text)
    if term_matches is None:
        term_matches = nlp.term_matcher().find(tokens)
    fields = [m for m in term_matches if m.kind == "field"]
    industry_spans = {j for m in term_matches if m.kind in ("industry", "unsupported") for j in range(m.start, m.end)}
    cues = _find_cues(tokens, industry_spans)
    clause = _clause_ids(tokens)

    def dist(f: nlp.Match, c: nlp.Match) -> tuple[int, int]:
        d = 0 if c.start >= f.start and c.end <= f.end else (
            c.start - f.end + 1 if c.start >= f.end else f.start - c.end + 1)
        return (d, 0 if c.start >= f.start else 1)  # tie → the cue after the field

    def label_of(c: nlp.Match) -> str:
        lab = c.values[0]
        return _FLIP[lab] if lab in _FLIP and _negated(tokens, clause, c) else lab

    inside_any = lambda c: any(c.start >= g.start and c.end <= g.end for g in fields)  # noqa: E731
    # Each explicit cue belongs to its nearest field in the same clause.
    owner: dict[int, int] = {}
    for ci, c in enumerate(cues):
        same = [fi for fi, f in enumerate(fields) if clause[f.start] == clause[c.start]]
        if same and not inside_any(c):
            owner[ci] = min(same, key=lambda fi: dist(fields[fi], c))

    out = Parsed()
    labels: dict[str, tuple[str, str]] = {}
    for fi, f in enumerate(fields):
        key = f.values[0]
        if key not in out.mentioned:
            out.mentioned.append(key)
        cid = clause[f.start]
        inside = [c for c in cues if c.start >= f.start and c.end <= f.end]
        explicit = [c for c in cues if clause[c.start] == cid and not inside_any(c)]
        owned = [c for ci, c in enumerate(cues) if owner.get(ci) == fi]
        if not (explicit or inside):
            if _negated_mention(tokens, clause, f) or _elliptical_negation(tokens, clause, f, cues):
                # "we don't ship by sea", "we will not export": a negated mention means it doesn't matter
                labels[key] = ("low", " ".join(tokens[max(0, f.start - NEGATION_WINDOW):f.end]))
                if key in out.unparsed_fields:
                    out.unparsed_fields.remove(key)
            elif _clause_has_negator(tokens, clause, f):
                # "port distance doesn't bother us", "we can't skimp on power": a negation we could not
                # read. Guessing could flip the meaning, so the router asks instead.
                if key not in out.uncertain:
                    out.uncertain.append(key)
            elif default_label:
                labels[key] = (default_label, " ".join(tokens[f.start:f.end]))
            elif key not in labels:
                out.unparsed_fields.append(key)
            continue
        if owned:
            # Several cues on one field ("must have reliable electricity"): the strongest wins, and a
            # negative statement beats a positive one ("I don't need cheap land" is low).
            cue = min(owned, key=lambda c: (_PRIORITY.index(label_of(c)), dist(f, c)))
        else:
            cue = min(explicit or inside, key=lambda c: dist(f, c))
        label = label_of(cue)
        if _elliptical_negation(tokens, clause, f, cues):
            label = "low"  # "port is critical and water is not"
        if _aversive(tokens, clause, f) and (label != cue.values[0] or cue.text in AVERSION_CUES):
            # "we don't want expensive land", "avoid expensive land": cheap land matters
            label = "high"
        lo, hi = min(f.start, cue.start), max(f.end, cue.end)
        labels[key] = (FOLD.get(label, label), " ".join(t for t in tokens[lo:hi]))
        if key in out.unparsed_fields:
            out.unparsed_fields.remove(key)

    out.modifiers = [{"field": k, "label": lab, "span": span} for k, (lab, span) in labels.items()]
    return out


def apply(base: dict[str, float], modifiers: dict[str, str], overrides: dict[str, float] | None = None) -> dict[str, float]:
    """Effective weights (§5.4). Modifiers multiply the *base* weights (a zero-base field raised to
    high/critical first gets a 0.05 floor). Slider overrides are absolute shares of the final vector;
    the remaining fields are renormalized to fill what is left."""
    overrides = {k: float(v) for k, v in (overrides or {}).items()}
    raw = {}
    for k, w in base.items():
        if k in overrides:
            continue
        lab = modifiers.get(k)
        if lab:
            if w == 0 and lab in ("high", "critical"):
                w = ZERO_BASE_FLOOR
            w *= MULTIPLIERS[lab]
        raw[k] = w
    fixed = sum(overrides.values())
    rest = sum(raw.values())
    if rest <= 0 < len(raw) and fixed < 1:
        # Every other slider is at 0: the remainder is split evenly across them (§5.5).
        out = {**{k: (1 - fixed) / len(raw) for k in raw}, **overrides}
    elif fixed >= 1 or rest <= 0:
        total = fixed + rest
        out = {**{k: v / total for k, v in raw.items()}, **{k: v / total for k, v in overrides.items()}}
    else:
        out = {**{k: v * (1 - fixed) / rest for k, v in raw.items()}, **overrides}
    return {k: out.get(k, 0.0) for k in base}
