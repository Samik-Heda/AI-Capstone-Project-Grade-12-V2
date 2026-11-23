"""Industry resolver (spec §7.3): lexicon → typo tolerance → embedding kNN → clarify.

Deterministic apart from the embedding tier, which is itself deterministic for a given model.
There is never a silent default to general manufacturing: a bare generic word ("factory") maps
to general only when nothing else in the text could name a product."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from rapidfuzz import fuzz
from rapidfuzz.distance import OSA, Levenshtein

from . import config, nlp
from .embeddings import INDEX

GENERAL = "preset:general_manufacturing"


_OVERRIDE: tuple[float, float] | None = None


def thresholds() -> tuple[float, float]:
    """(τ, δ) for the embedding tier, from config.toml [intent] (calibrated in Phase 4)."""
    if _OVERRIDE is not None:
        return _OVERRIDE
    c = config.load().get("intent", {})
    return float(c.get("tau", 0.62)), float(c.get("delta", 0.05))


def set_thresholds(tau: float | None, delta: float | None = None) -> None:
    """Calibration hook: override (τ, δ); pass None to go back to config.toml."""
    global _OVERRIDE
    _OVERRIDE = None if tau is None else (tau, delta)


@dataclass
class Resolution:
    status: str                      # match | ambiguous | unsupported | out_of_scope | low | none
    profile_id: str | None = None
    confidence: float = 0.0
    tier: str | None = None          # lexicon | typo | embedding | generic
    candidates: list[str] = field(default_factory=list)
    term: str | None = None
    resource: str | None = None
    has_signal: bool = False         # the text looks like an industry request
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "match"


@lru_cache(maxsize=1)
def _fuzzy_terms() -> list[tuple[str, str, str]]:
    """(term, kind, value) for single- and two-word industry/unsupported terms of ≥5 characters."""
    lex = nlp.industry_lexicon()
    out = []
    for pid, terms in lex["profiles"].items():
        out += [(t, "industry", pid) for t in terms]
    out += [(t, "unsupported", r) for t, r in lex["unsupported"].items()]
    return [(t, k, v) for t, k, v in out if len(t.replace(" ", "")) >= 5 and len(t.split()) <= 2]


@lru_cache(maxsize=1)
def known_vocab() -> frozenset[str]:
    """Every word the lexicons already understand. Fuzzy matching never touches these, so 'plant'
    can never be read as a typo of 'paint'."""
    from . import db
    words = set(nlp.STOPWORDS)
    lex, flex = nlp.industry_lexicon(), nlp.field_lexicon()
    phrases = [t for ts in lex["profiles"].values() for t in ts] + list(lex["unsupported"]) + lex["generic"]
    phrases += [s for ss in nlp.field_synonyms().values() for s in ss] + flex["stoplist"]
    phrases += [p for ps in flex["intensity"].values() for p in ps] + flex["negators"]
    for d in db.districts():
        phrases += [d["name"], *d["alt_names"]]
    for p in phrases:
        for t in nlp.tokenize(p):
            words.add(t)
            words.add(nlp.lemma(t))
    return frozenset(words)


def _typo_match(word: str, term: str) -> bool:
    if fuzz.ratio(word, term) >= 88:
        return True
    d = OSA.distance(word, term)
    if len(term) < 7:
        # Short terms: only a swapped letter pair ("diary" → "dairy"). A one-letter substitution
        # between short words is usually another real word ("space" vs "spice").
        return d == 1 and Levenshtein.distance(word, term) == 2
    return d <= 2 and len(word) >= 8 and word[0] == term[0] and abs(len(word) - len(term)) <= 1


def typo_candidates(tokens: list[str], covered: set[int]) -> list[tuple[str, str, str, str]]:
    """[(word, term, kind, value)] for unknown tokens (and bigrams) close to a lexicon term."""
    vocab = known_vocab()
    unknown = [i for i, t in enumerate(tokens)
               if i not in covered and t.isalpha() and len(t) >= 4 and t not in vocab and nlp.lemma(t) not in vocab]
    hits = []
    for term, kind, value in _fuzzy_terms():
        parts = term.split()
        for i in unknown:
            if len(parts) == 1:
                if _typo_match(tokens[i], term):
                    hits.append((tokens[i], term, kind, value))
            elif i + 1 < len(tokens) and _typo_match(f"{tokens[i]} {tokens[i + 1]}", term):
                hits.append((f"{tokens[i]} {tokens[i + 1]}", term, kind, value))
    return hits


INDUSTRY_VERBS = {"make", "makes", "manufacture", "manufactures", "produce", "produces", "process", "assemble",
                  "assembles", "build", "set", "open", "start", "invest", "investing", "launch"}


BUSINESS_NOUNS = {"company", "business", "firm", "startup", "operator", "provider", "service", "services",
                  "enterprise", "agency", "cooperative"}


def _names_business(tokens: list[str], m: nlp.Match) -> bool:
    """The term is, or directly precedes, a business noun: "transport company", "courier service"."""
    span = tokens[m.start:m.end]
    return any(t in BUSINESS_NOUNS for t in span) or (m.end < len(tokens) and tokens[m.end] in BUSINESS_NOUNS)


def content_query(tokens: list[str], term_matches: list[nlp.Match]) -> str:
    """The words that describe the business: no stopwords, generic words, field words or verbs."""
    skip = {j for m in term_matches if m.kind in ("generic", "field", "stop") for j in range(m.start, m.end)}
    return " ".join(t for i, t in enumerate(tokens) if i not in skip and t not in nlp.STOPWORDS
                    and t not in nlp.SPLIT_PUNCT and t not in INDUSTRY_VERBS and not t.isdigit())


def resolve(text: str, tokens: list[str], term_matches: list[nlp.Match], covered_extra: set[int] | None = None,
            allow_embedding: bool = True) -> Resolution:
    """`covered_extra` marks token positions already explained elsewhere (district mentions)."""
    covered = set(covered_extra or set())
    for m in term_matches:
        covered.update(range(m.start, m.end))
    # "steel-free castings", "plastic free packaging": the term is what the product is NOT
    term_matches = [m for m in term_matches if not (m.kind in ("industry", "unsupported") and m.end < len(tokens)
                                                    and tokens[m.end] == "free")]
    industry = [m for m in term_matches if m.kind == "industry"]
    # "cold storage for vaccines", "cartons for pharma companies": what follows "for" is the customer.
    if "for" in tokens:
        cut = tokens.index("for")
        before = [m for m in industry if m.end <= cut]
        if before and len(before) < len(industry):
            industry = before
    unsupported = [m for m in term_matches if m.kind == "unsupported"]
    generic = [m for m in term_matches if m.kind == "generic"]

    # Tier 1: lexicon
    if industry:
        pids = list(dict.fromkeys(p for m in industry for p in m.values))
        term = industry[0].text
        if len(pids) == 1:
            return Resolution("match", pids[0], 1.0, "lexicon", term=term, has_signal=True)
        terms = " / ".join(m.text for m in industry)
        # A term naming the business itself ("transport company", "courier firm") outranks things the
        # business merely uses or handles ("a transport company with 200 trucks").
        owners = [m for m in industry if len(m.values) == 1 and _names_business(tokens, m)]
        if len({m.values[0] for m in owners}) == 1:
            return Resolution("match", owners[0].values[0], 1.0, "lexicon", term=owners[0].text, has_signal=True,
                              detail={"conflict": pids, "rule": "business noun"})
        # Most distinct terms wins ("reefer trucks and cold rooms": two cold-chain terms, one vehicle).
        counts: dict[str, int] = {}
        for m in industry:
            if len(m.values) == 1:
                counts[m.values[0]] = counts.get(m.values[0], 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        if len(ranked) > 1 and ranked[0][1] > ranked[1][1] and not any(len(m.values) > 1 for m in industry):
            return Resolution("match", ranked[0][0], 0.95, "lexicon", term=terms, has_signal=True,
                              detail={"conflict": pids, "rule": "majority", "counts": counts})
        # Two different terms naming two profiles ("molasses-based alcohol", "pomegranate juice
        # bottling"): let the exemplars decide between just those profiles. One term that itself
        # belongs to two profiles ("cotton ginning") is a genuine ambiguity and is always asked.
        if allow_embedding and INDEX.available and not any(len(m.values) > 1 for m in industry):
            knn = INDEX.knn(content_query(tokens, term_matches) or text, restrict=pids)
            if knn is not None:
                votes = knn["votes"]
                margin = votes[0][1] - (votes[1][1] if len(votes) > 1 else 0.0)
                detail = {"conflict": pids, "margin": round(margin, 3), "neighbors": knn["neighbors"][:3]}
                if margin >= thresholds()[1]:
                    return Resolution("match", votes[0][0], 0.95, "lexicon+embedding", term=terms,
                                      has_signal=True, detail=detail)
        return Resolution("ambiguous", candidates=pids, term=terms, tier="lexicon", has_signal=True)
    if unsupported:
        m = unsupported[0]
        return Resolution("unsupported", term=m.text, resource=m.values[0], tier="lexicon", has_signal=True)
    scope = [m for m in term_matches if m.kind == "out_of_scope"]
    if scope:
        return Resolution("out_of_scope", term=scope[0].text, tier="lexicon", has_signal=True)

    # Tier 2: typo tolerance
    typos = typo_candidates(tokens, covered)
    if typos:
        ind = list(dict.fromkeys(v for _, _, k, v in typos if k == "industry"))
        if len(ind) == 1:
            w, t, _, _ = next(x for x in typos if x[2] == "industry")
            return Resolution("match", ind[0], 0.9, "typo", term=t, has_signal=True, detail={"typed": w})
        if len(ind) > 1:
            return Resolution("ambiguous", candidates=ind, tier="typo", has_signal=True)
        w, t, _, res = typos[0]
        return Resolution("unsupported", term=t, resource=res, tier="typo", has_signal=True, detail={"typed": w})

    residual = nlp.residual_content(tokens, covered)
    fields = any(m.kind == "field" for m in term_matches)
    has_signal = bool(generic) or (not fields and any(t in INDUSTRY_VERBS for t in tokens))

    # Generic-only: "a factory" → general; "space laser factory" → look at the product noun
    if generic and not residual:
        return Resolution("match", GENERAL, 1.0, "generic", term=generic[0].text, has_signal=True)

    # Tier 3: embedding kNN
    if allow_embedding and INDEX.available:
        # Embed the product words only: generic words ("factory", "unit") pull every query toward
        # exemplars that happen to share them ("bicycle factory" → "sugar factory").
        query = " ".join(t for t in residual if t not in INDUSTRY_VERBS) or text
        knn = INDEX.knn(query)
        if knn is not None:
            tau, delta = thresholds()
            votes = knn["votes"]
            margin = votes[0][1] - (votes[1][1] if len(votes) > 1 else 0.0)
            detail = {"top_sim": round(knn["top_sim"], 3), "margin": round(margin, 3), "votes": votes[:3],
                      "neighbors": knn["neighbors"]}
            if knn["top_sim"] >= tau and margin >= delta:
                return Resolution("match", votes[0][0], round(knn["top_sim"], 3), "embedding",
                                  has_signal=True, detail=detail)
            cands = [p for p, _ in votes[:3]]
            for p in INDEX.nearest_profiles(query, 3) or []:
                if len(cands) >= 3:
                    break
                if p not in cands:
                    cands.append(p)
            return Resolution("low", candidates=cands[:3], tier="embedding", has_signal=has_signal, detail=detail)

    # Tier 4: nothing confident
    return Resolution("none", has_signal=has_signal, detail={"residual": residual})


def unsupported_chips(term: str) -> list[str]:
    """The two closest supported profiles (curated in the lexicon), plus General Manufacturing."""
    lex = nlp.industry_lexicon()
    key = next((t for t in lex["unsupported_nearest"] if nlp.phrase_key(t) == nlp.phrase_key(term)), None)
    chips = list(lex["unsupported_nearest"].get(key) or [GENERAL])
    if GENERAL not in chips:
        chips.append(GENERAL)
    return chips
