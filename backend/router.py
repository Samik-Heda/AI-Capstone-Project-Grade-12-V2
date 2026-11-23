"""Message router (spec §7.2): text → kind → state change → templated reply.

Rules run in a fixed order and the first match wins. No LLM is involved anywhere here."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from functools import lru_cache

from rapidfuzz import fuzz

from . import advisor, db, facts, intent, nlp, scope, templates
from . import modifiers as mod
from .embeddings import INDEX
from .session import Session

RESET_RE = re.compile(r"^(reset|start over|clear|start again|restart)( the)?( weights| everything| all| it)?( please)?[.! ]*$")
HELP_RE = re.compile(r"^(hello|hi|hey|hiya|good (morning|afternoon|evening)|help|help me|what can you do|what do you do|"
                     r"how does this work|how do i use this|what is this|who are you|menu|examples?)( there)?[?.! ]*$")
COMPARE_WORDS = {"compare", "comparison", "vs", "versus", "against"}
EXPLAIN_RE = re.compile(r"\b(why|explain|what makes|how did|how does|how do|how is|how good|tell me about|what about|strength|strengths|"
                        r"weakness|weaknesses|rank|ranked|ranks|ranking|position|score|scored|doing|perform|performs|details?|"
                        r"pros|cons|good for|bad for)\b")
PRONOUNS = {("it",), ("its",), ("there",), ("that", "district"), ("this", "district"), ("that", "one"), ("the", "same")}
ORDINALS = {"1": 0, "first": 0, "one": 0, "2": 1, "second": 1, "two": 1, "3": 2, "third": 2, "three": 2,
            "4": 3, "5": 4, "6": 5, "7": 6, "8": 7}
DISTRICT_DENY = {"bid"}  # Beed's alt name is also a common English word
NARRATED_KINDS = ("apply_preset", "current_preset", "adjust", "compare", "explain_district", "chat", "clarify_answer",
                  "ask")
# §7.2 rule 3: explicit choices. "Switch to dairy" is *not* here: it is a suggestion with an Apply chip.
APPLY_RE = re.compile(r"^(?:apply|use|go with|choose|select)(?: the)? (.+?)(?: preset| profile)?$")
YES_PHRASES = {"yes", "yes please", "ok", "okay", "sure", "apply", "apply it", "do it", "go ahead", "yep", "yeah"}
PICK_PHRASES = {"pick another", "pick an industry", "pick another industry", "choose another", "pick a preset",
                "show presets", "show industries", "show all industries"}
CANCEL_PHRASES = {"cancel", "never mind", "nevermind", "do not save", "dont save", "no"}
# §7.2 rule 3b: "save this", "save this as <name>", "keep this profile", "remember this", the Save chip
SAVE_RE = re.compile(r"^(?:please )?(?:save|keep|remember)(?: (?:this|it|that|these|my|the|current))?"
                     r"(?: (?:profile|weights|settings|priorities|setup|one))?(?: (?:for me|please))?"
                     r"(?: as (?P<name>.+))?$")
SHOW_ALL_RE = re.compile(r"\b(all 36|show all|full list|all districts|whole list|entire list)\b")

# §18.2: a previewed change (an advisor answer) waits for its Apply chip or a yes.
APPLY_CHANGE_PHRASES = {"apply this change", "apply these changes", "apply the change", "apply change", "apply changes"}
KEEP_PHRASES = {"keep as is", "keep it as is", "keep it", "leave it", "no thanks", "no thank you", "no", "cancel",
                "keep current", "keep the current priorities"}
APPLY_CHANGE_CHIP = {"label": "Apply this change", "value": "Apply this change"}
# §18.3 "make a business brief", "print a report", "one-pager for my bank"
BRIEF_RE = re.compile(r"\b(business brief|brief|report|one pager|one-pager|printable|print|pdf|"
                      r"summary for (?:my|the|a) (?:bank|banker|partner|investor|family)s?)\b")
KEEP_CHIP = {"label": "Keep as is", "value": "Keep as is"}
# §18.1: an answer the rules could not read gets a re-ask (and the LLM's reading, when it has one)
REASK_CHIPS = [{"label": "Yes, a lot", "value": "Yes, a lot"}, {"label": "Yes", "value": "Yes"},
               {"label": "Not really", "value": "Not really"}, {"label": "Not sure", "value": "Not sure"}]
INTERPRET_MAX_TOKENS = 40
# §19.1 a question gets an answer, never a weight change ("how much water does a chip fab need")
QUESTION_RE = re.compile(r"^(what|whats|which|how|why|when|where|who|any|anything|tell me|explain|list|give me|"
                         r"suggest|recommend|advise|i wonder)\b")
# "do I need…", "is there…", "can we…" are questions; "do not care about water" is a command
AUX_QUESTION_RE = re.compile(r"^(do|does|did|is|are|can|could|would|will|should|shall)\s+(i|we|you|it|they|there|this|"
                             r"that|these|those|my|our|the|a|an|any|anything|someone)\b")
COMMAND_RE = re.compile(r"\b(make|set|increase|decrease|raise|lower|boost|reduce|ignore|prioriti[sz]e|weight|"
                        r"bump|drop|add|remove)\b")
# §19.2 what-if: a preview of a priority change, applied only with its chip
WHAT_IF_RE = re.compile(r"^(what if|what would happen if|what happens if|suppose|supposing|imagine|if i|if we|"
                        r"and if|how about if|what about if)\b")
MORE_CHIP = {"label": "Tell me more", "value": "action:more"}
# a what-if is a priority change only with a priority cue; "what if I can't find workers?" is a question
WHAT_IF_CUE_RE = re.compile(r"\b(more|less|cheaper|costlier|better|worse|closer|farther|higher|lower|bigger|smaller|"
                            r"important|critical|matters?|mattered|ignore|ignored|did not need|didn t need|do not need|"
                            r"don t need|no need|not need|not needed|care)\b")
SWITCH_CUES = intent.INDUSTRY_VERBS | {"actually", "instead", "switch", "change", "rather", "industry", "sector",
                                       "plant", "factory", "unit", "business", "start", "build", "open", "set"}
# A request or a question, not an answer to the advisor ("show me all 36", "which is best?")
NOT_ANSWER_RE = re.compile(r"^(what|which|where|who|whom|why|how|when|show|list|tell|give|explain|compare|can|could|"
                           r"would you|is there|are there|please show)\b")


@dataclass
class Understanding:
    tokens: list[str]
    districts: list[int] = field(default_factory=list)
    used_pronoun: bool = False
    resolution: intent.Resolution | None = None
    parsed: mod.Parsed | None = None
    answer: str | None = None        # advisor answer label (§18.2)
    fallback: bool = False           # nothing matched: the last-resort help reply

    def public(self) -> dict:
        r = self.resolution
        return {
            "districts": self.districts,
            "industry": None if r is None else {
                "status": r.status, "profile_id": r.profile_id, "confidence": r.confidence, "tier": r.tier,
                "candidates": r.candidates, "term": r.term},
            "modifiers": self.parsed.modifiers if self.parsed else [],
            "unparsed_fields": self.parsed.unparsed_fields if self.parsed else [],
        }


# ---------- district mentions (§7.5) ----------

@lru_cache(maxsize=1)
def district_matcher() -> nlp.PhraseMatcher:
    m = nlp.PhraseMatcher(stem=False)
    for d in db.districts():
        for name in [d["name"], *d["alt_names"]]:
            if name.lower() not in DISTRICT_DENY:
                m.add(name, "district", d["id"])
    return m


@lru_cache(maxsize=1)
def _single_names() -> list[tuple[str, int]]:
    out = []
    for d in db.districts():
        for name in [d["name"], *d["alt_names"]]:
            toks = nlp.tokenize(name)
            if len(toks) == 1 and len(toks[0]) >= 5 and name.lower() not in DISTRICT_DENY:
                out.append((toks[0], d["id"]))
    return out


def find_districts(tokens: list[str]) -> tuple[list[int], set[int]]:
    """District ids in mention order (deduplicated) and the token positions they cover."""
    ids, covered = [], set()
    for m in district_matcher().find(tokens):
        covered.update(range(m.start, m.end))
        if m.values[0] not in ids:
            ids.append(m.values[0])
    vocab = intent.known_vocab()
    for i, t in enumerate(tokens):
        if i in covered or len(t) < 5 or not t.isalpha() or t in vocab:
            continue
        best = max(_single_names(), key=lambda x: fuzz.ratio(t, x[0]))
        if fuzz.ratio(t, best[0]) >= 90:
            covered.add(i)
            if best[1] not in ids:
                ids.append(best[1])
    return ids, covered


def _has_pronoun(tokens: list[str]) -> bool:
    for i in range(len(tokens)):
        if (tokens[i],) in PRONOUNS or tuple(tokens[i:i + 2]) in PRONOUNS:
            return True
    return False


# ---------- routing ----------

def understand(s: Session, text: str) -> tuple[str, Understanding]:
    """Classify a message. Returns (kind, understanding); no state is changed here."""
    norm = nlp.normalize(text)
    tokens = nlp.tokenize(text)
    u = Understanding(tokens)
    bare = re.sub(r"[^a-z0-9 ]", " ", norm).strip()
    bare = re.sub(r"\s+", " ", bare)

    if RESET_RE.match(bare) or bare in ("reset weights", "reset the weights"):
        return "reset", u
    if HELP_RE.match(bare):
        return "help", u
    if norm.startswith("action:pick") or bare in PICK_PHRASES:
        return "pick_preset", u
    if s.pending_save is not None:
        return ("save_cancel" if bare in CANCEL_PHRASES else "save_profile"), u
    verdict = scope.check(text)
    # §18.2: while the advisor's question is open, a place is context ("we export to Dubai"), not a request
    if verdict.out_of_scope and not (s.pending_question and verdict.reason == "place"):
        return "refusal", u
    if s.pending_change is not None:
        if bare in APPLY_CHANGE_PHRASES or bare in YES_PHRASES:
            return "apply_change", u
        if bare in KEEP_PHRASES:
            return "decline_change", u

    # 3: an explicit choice — an Apply chip, a yes to the pending suggestion, or a clarify chip
    if _apply_target(s, bare) is not None:
        return "apply_preset", u
    if s.pending_clarify and _clarify_choice(s, bare, tokens) is not None:
        return "clarify_answer", u
    # 3b: save the current profile from chat
    if SAVE_RE.match(bare):
        return "save_profile", u

    u.districts, dcovered = find_districts(tokens)
    if not u.districts and s.focus_district and _has_pronoun(tokens):
        u.districts, u.used_pronoun = [s.focus_district], True
    elif len(u.districts) == 1 and s.focus_district and s.focus_district != u.districts[0] and _has_pronoun(tokens) \
            and set(tokens) & COMPARE_WORDS:
        u.districts.append(s.focus_district)

    terms = nlp.term_matcher().find(tokens, blocked=dcovered)

    if not u.districts and BRIEF_RE.search(norm):
        return "brief", u

    # §18.2: a short answer to the advisor's open question ("yes, a lot", "not really")
    if s.pending_question and not u.districts:
        qf = s.pending_question["field"]
        lab = advisor.classify_answer(text)
        other = mod.parse(text, tokens, terms).as_map()
        if lab is not None and (not other or set(other) == {qf}):
            u.answer = lab
            return "advisor_answer", u
        if lab is None and set(other) == {qf} and other[qf] != "exclude":
            u.answer = other[qf]  # "land matters less for us": the question's own factor, read by the rules
            return "advisor_answer", u
        if lab is None and not other and set(mod.parse(text, tokens, terms).mentioned) == {qf}:
            return "unread_answer", u  # "the plot can be tiny": about the question, but how much? (LLM reads it)

    if len(u.districts) >= 2 or (u.districts and set(tokens) & COMPARE_WORDS):
        return "compare", u
    if u.districts and EXPLAIN_RE.search(norm):
        u.parsed = mod.parse(text, tokens, terms)
        return "explain_district", u

    u.parsed = mod.parse(text, tokens, terms)
    has_generic = any(m.kind == "generic" for m in terms)
    has_verb = not u.parsed.mentioned and any(t in intent.INDUSTRY_VERBS for t in tokens)
    allow_emb = has_generic or has_verb or (not s.ranking and not u.parsed.mentioned and not u.districts)
    # §18.2: a reply to the advisor's question is not an industry description ("the process is dry" is not a
    # dry port); explicit industry words still match through the lexicon tier
    allow_emb = allow_emb and not s.pending_question
    u.resolution = intent.resolve(text, tokens, terms, covered_extra=dcovered, allow_embedding=allow_emb)
    r = u.resolution

    # §19.2/§19.1: with an industry on, a what-if is previewed and a question is answered; neither
    # changes weights. A new industry named in the message still goes through its Apply chip.
    other_industry = r.ok and r.tier != "generic" and r.profile_id != s.profile_id
    if s.profile_id and not u.districts:
        if WHAT_IF_RE.match(norm.strip()) and not other_industry:
            if not WHAT_IF_CUE_RE.search(norm):
                return "ask", u  # a scenario ("what if I can't find workers?"), not a priority change
            if not u.parsed.modifiers:
                u.parsed = mod.parse(text, tokens, terms, default_label="high")
            return ("what_if" if u.parsed.modifiers else "ask"), u
        if is_question(norm) and not SHOW_ALL_RE.search(norm) and not COMMAND_RE.search(norm):
            return "ask", u  # a question naming another industry is answered too, with its Apply chip

    # A bare generic word or an unconfident guess next to field words is a priority change, not a
    # new industry ("it should be an export oriented unit").
    weak = (r.ok and r.tier == "generic") or r.status in ("low", "none")
    if weak and u.parsed.mentioned:
        return _priorities(s, u, text, tokens, terms), u
    if r.ok:
        if r.profile_id != s.profile_id:
            return "suggest_preset", u  # 6: nothing switches until the Apply chip
        if u.parsed.modifiers:
            return "adjust", u          # same industry: its modifiers are an adjustment
        return "current_preset", u
    if r.status in ("ambiguous", "unsupported", "out_of_scope") or (r.has_signal and r.status in ("low", "none")):
        return "clarify", u
    if u.parsed.mentioned:
        return _priorities(s, u, text, tokens, terms), u
    if u.districts and not u.used_pronoun:
        return "explain_district", u
    if s.ranking:
        return "chat", u
    u.fallback = True
    return "help", u


def is_question(norm: str) -> bool:
    n = norm.strip()
    return n.endswith("?") or bool(QUESTION_RE.match(n)) or bool(AUX_QUESTION_RE.match(n))


def _priorities(s: Session, u: Understanding, text: str, tokens: list[str], terms: list[nlp.Match]) -> str:
    """Field words without a (new) industry: adjust the session, or — with no industry yet — offer
    General Manufacturing with the priorities pending (§7.2 rule 7b). Never applied silently."""
    if not u.parsed.modifiers:
        u.parsed = mod.parse(text, tokens, terms, default_label="high")
    if not u.parsed.modifiers and u.parsed.uncertain:
        return "clarify_modifier"
    return "adjust" if s.profile_id else "suggest_general"


def _apply_target(s: Session, bare: str) -> str | None:
    """'Apply Pharmaceuticals' / 'Use General Manufacturing' (chips), or a yes to the pending suggestion."""
    if s.pending_suggest and bare in YES_PHRASES:
        return s.pending_suggest["preset_id"]
    m = APPLY_RE.match(bare)
    if not m:
        return None
    want = m.group(1).strip()
    if s.pending_suggest and want in ("it", "this", "that", "that one", "this one"):
        return s.pending_suggest["preset_id"]
    for p in db.profiles():
        if p["kind"] != "user" and want in (_bare(p["name"]), p["id"].split(":", 1)[1].replace("_", " ")):
            return p["id"]
    return None


def _bare(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", nlp.normalize(name))).strip()


def _clarify_choice(s: Session, bare: str, tokens: list[str]) -> str | None:
    cands = s.pending_clarify["candidates"]
    if bare in ORDINALS and ORDINALS[bare] < len(cands):
        return cands[ORDINALS[bare]]
    for pid in cands:
        if bare in (pid.lower(), _bare(templates.profile_name(pid)), pid.split(":", 1)[1].replace("_", " ")):
            return pid
    picked = {p for m in nlp.term_matcher().find(tokens) if m.kind == "industry" for p in m.values}
    hit = [p for p in cands if p in picked]
    return hit[0] if len(hit) == 1 else None


def _save_name(text: str) -> str | None:
    m = SAVE_RE.match(_bare(text))
    if not m or not m.group("name"):
        return None
    name = text.strip().strip(".!")
    idx = name.lower().rfind(" as ")
    name = name[idx + 4:].strip() if idx >= 0 else m.group("name")
    return None if _bare(name) in ("my profile", "a profile", "profile") else name[:80]


def save_current(s: Session, name: str) -> dict:
    from . import profiles
    base = s.profile_id
    base_preset = base if base and not base.startswith("user:") else None
    p = profiles.create(name, s.weights, s.constraints,
                        rationale=profiles.rationale_for(base_preset, s.weights, s.base_weights, s.modifiers or None)
                        if base_preset else f"Saved from chat, starting from {s.profile_name}.",
                        base_profile_id=base_preset,
                        provenance={"source": "chat", "modifiers": s.modifiers, "overrides": s.slider_overrides})
    s.pending_save = None
    s.set_profile(p["id"])
    return p


def handle(s: Session, text: str, narration_ready: bool = False) -> dict:
    t0 = time.perf_counter()
    kind, u = understand(s, text)
    route_ms = (time.perf_counter() - t0) * 1000
    s.turn_no += 1
    user_turn = s.add_turn("user", text, kind)
    chips: list[dict] = []
    fact: dict | None = None
    prev_leader_name = (s.leader() or {}).get("name")
    question = s.pending_question
    s.pending_question = None  # one chance: answered now, or dropped when the user moves on
    if kind not in ("apply_change", "decline_change", "refusal", "help"):
        s.pending_change = None

    if kind not in ("clarify_answer", "clarify", "refusal"):
        s.pending_clarify = None
    if kind not in ("suggest_preset", "suggest_general", "pick_preset", "refusal", "help", "apply_preset"):
        s.pending_suggest = None  # a suggestion lives until the next real step

    if kind == "reset":
        s.pending_save = None
        if s.profile_id:
            s.reset()
            reply = templates.reset(s.profile_name, s.leader())
        else:
            reply = "Nothing to reset yet. " + templates.help_text(False)
            chips = templates.chips(templates.HELP_CHIPS)

    elif kind == "help":
        reply = templates.help_text(bool(s.ranking))
        chips = templates.chips(templates.HELP_CHIPS)

    elif kind == "refusal":  # out of scope (scope.py): no state change
        reply = templates.REFUSAL
        chips = templates.chips(templates.HELP_CHIPS[:4])

    elif kind == "pick_preset":  # the UI opens the preset menu; nothing changes here
        reply = templates.pick_preset()
        chips = templates.preset_menu()

    elif kind == "apply_preset":
        bare = _bare(text)
        pid = _apply_target(s, bare)
        pending = s.pending_suggest if s.pending_suggest and s.pending_suggest["preset_id"] == pid else None
        changes = dict(pending["modifiers"]) if pending else {}
        s.set_profile(pid, apply_pending=bool(pending))
        s.pending_suggest = None
        reply = templates.rank(s.profile_name, s.ranking, s.excluded, changes, s.weights)
        if changes:
            chips = [templates.SAVE_CHIP]
        fact = {"top": _top(s), "changes": changes}

    elif kind == "clarify_answer":
        bare = _bare(text)
        pid = _clarify_choice(s, bare, u.tokens)
        s.pending_clarify = None
        s.set_profile(pid)
        reply = templates.rank(s.profile_name, s.ranking, s.excluded, {}, s.weights)
        fact = {"top": _top(s)}

    elif kind == "save_profile":
        if not s.profile_id:
            reply = templates.save_needs_profile()
            chips = [templates.PICK_INDUSTRY]
            s.pending_save = None
        else:
            name = (text.strip()[:80] or None) if s.pending_save is not None else _save_name(text)
            if name:
                p = save_current(s, name)
                reply = templates.saved(p["name"])
                fact = {"profile": {k: p[k] for k in ("id", "name", "kind")}}
            else:
                suggested = f"{s.profile_name} (custom)"
                s.pending_save = {"suggested": suggested}
                reply = templates.ask_save_name()
                chips = templates.chips([suggested])

    elif kind == "save_cancel":
        s.pending_save = None
        reply = "OK, not saved."

    elif kind == "suggest_preset":
        r = u.resolution
        changes = u.parsed.as_map() if u.parsed else {}
        s.pending_suggest = {"preset_id": r.profile_id, "modifiers": changes}
        reply = templates.suggest_preset(templates.profile_name(r.profile_id), changes, s.profile_name)
        if u.parsed and u.parsed.unparsed_fields:
            reply += templates.unparsed_note(u.parsed.unparsed_fields)
        chips = [templates.apply_chip(r.profile_id), templates.PICK_ANOTHER]

    elif kind == "suggest_general":
        changes = u.parsed.as_map()
        s.pending_suggest = {"preset_id": intent.GENERAL, "modifiers": changes}
        reply = templates.suggest_general(changes)
        chips = [{"label": f"Use {templates.profile_name(intent.GENERAL)}",
                  "value": f"Use {templates.profile_name(intent.GENERAL)}"}, templates.PICK_INDUSTRY]

    elif kind == "current_preset":
        reply = templates.already_on(s.profile_name, s.ranking)
        fact = {"top": _top(s)}

    elif kind == "adjust":
        before, prev_leader = dict(s.weights), s.leader()
        changes = u.parsed.as_map()
        s.apply_modifiers(changes)
        reply = templates.adjust(changes, before, s.weights, prev_leader, s.leader())
        chips = [templates.SAVE_CHIP]
        fact = {"top": _top(s), "changes": changes}

    elif kind == "compare":
        note = ensure_ranking(s)
        a, b = u.districts[0], (u.districts[1] if len(u.districts) > 1 else None)
        if b is None:
            lead = s.leader()
            b = lead["district_id"] if lead and lead["district_id"] != a else _eligible_ids(s)[1]
        cmp = facts.compare(a, b, s.ranking, s.weights)
        reply = templates.compare(cmp, s.profile_name) + note
        if len(u.districts) > 2:
            reply += " (I compare two districts at a time; I used the first two you named.)"
        s.focus_district = a
        fact = {"compare": cmp}

    elif kind == "explain_district":
        note = ensure_ranking(s)
        did = u.districts[0]
        focus = u.parsed.mentioned if u.parsed else []
        reply = templates.explain(did, s.ranking, s.weights, s.profile_name, focus) + note
        s.focus_district = did
        fact = {"district_id": did, "entry": s.entry(did),
                "strengths": facts.strengths(did, s.weights), "weaknesses": facts.weaknesses(did, s.weights),
                "why_not_first": facts.why_not_first(did, s.ranking, s.weights),
                "focus": templates.focus_ranks(did, s.ranking, s.weights, focus), "note": note.strip(" ()") or None}

    elif kind == "clarify":
        reply, chips, pending = _clarify(u.resolution)
        s.pending_clarify = pending

    elif kind == "advisor_answer":
        f = question["field"]
        if u.answer == "unsure":
            reply = "No problem, I'll leave that as it is."
        else:
            reply, chips, fact = answer_turn(s, f, u.answer)

    elif kind == "apply_change":
        before, prev_leader = dict(s.weights), s.leader()
        changes = dict(s.pending_change["modifiers"])
        s.pending_change = None
        s.apply_modifiers(changes)
        reply = templates.adjust(changes, before, s.weights, prev_leader, s.leader())
        chips = [templates.SAVE_CHIP]
        fact = {"top": _top(s), "changes": changes}

    elif kind == "brief":  # the client opens the brief (POST /api/brief streams it)
        if s.profile_id:
            reply = f"Opening your business brief for **{s.profile_name}**. It fills in over a minute or two."
            fact = {"open_brief": True}
        else:
            reply = "Pick an industry first, then I can write the brief."
            chips = [templates.PICK_INDUSTRY]

    elif kind == "ask":  # §19.1 answered by the AI (auto note); the template covers offline
        needs = [n["need"] for n in db.industry_needs(s.profile_id)]
        reply = templates.ask_reply(s.profile_name, s.ranking, s.weights, needs)
        fact = {"needs": needs}
        mentioned = [f for f in (u.parsed.mentioned if u.parsed else []) if f not in s.modifiers]
        chips = [{"label": f"Make {templates.plain_lc(f)} matter more", "value": f"{templates.short(f).lower()} is important"}
                 for f in mentioned[:2]] + [MORE_CHIP]
        r = u.resolution
        if r is not None and r.ok and r.tier != "generic" and r.profile_id != s.profile_id:
            s.pending_suggest = {"preset_id": r.profile_id, "modifiers": {}}
            chips = [templates.apply_chip(r.profile_id)] + chips

    elif kind == "what_if":  # §19.2 a preview with Apply / Keep, never a change
        changes = u.parsed.as_map()
        p = advisor.preview(s, changes)
        s.pending_change, s.pending_suggest = {"modifiers": changes}, None
        reply = templates.what_if(changes, p)
        chips = [APPLY_CHANGE_CHIP, KEEP_CHIP]
        fact = {"preview": p, "changes": changes}

    elif kind == "unread_answer":  # replaced by the re-ask below
        reply = ""

    elif kind == "decline_change":
        s.pending_change = None
        reply = "OK, I'll keep the current priorities."

    elif kind == "clarify_modifier":
        keys = u.parsed.uncertain
        reply = templates.clarify_modifier([templates.plain_lc(f) for f in keys])
        chips = [c for f in keys for c in (
            {"label": f"{templates.plain(f)} matters more", "value": f"{templates.short(f).lower()} is important"},
            {"label": f"{templates.plain(f)} matters less", "value": f"{templates.short(f).lower()} is not important"})]

    else:  # chat
        reply = templates.chat(s.profile_name, s.ranking, s.weights)
        if SHOW_ALL_RE.search(nlp.normalize(text)):
            chips = [{"label": "Show all 36", "value": "action:show_all"}]
        fact = {"top": _top(s, 5)}

    if u.parsed and u.parsed.unparsed_fields and kind in ("adjust", "apply_preset"):
        reply += templates.unparsed_note(u.parsed.unparsed_fields)
        chips += [{"label": f"Make {templates.plain_lc(f)} important",
                   "value": f"{templates.short(f).lower()} is important"} for f in u.parsed.unparsed_fields]

    # §18.1 the rules gave up: say so plainly, and let the LLM try (POST /api/interpret)
    interp = _interpret_request(s, kind, u, text, question)
    if interp and interp["mode"] == "answer":
        kind = "advisor_reask"
        reply = (f"I didn't quite catch that. To recap: {question['text']} "
                 "You can answer in your own words, or tap one of these.")
        chips = list(REASK_CHIPS)
        fact = None
        s.pending_question = {**question, "reasked": True}  # still open once more: a typed yes/no works too
        s.pending_suggest = s.pending_clarify = None  # so a "yes" answers the question, not a stale suggestion

    # §18.2 advisor: a trade-off tip, follow-up chips and (sometimes) one open question
    tip = advisor.tip(s) if kind in advisor.RANK_KINDS and s.profile_id else None
    if tip:
        fact = {**(fact or {}), "tip": tip}
        reply += " " + advisor.tip_text(tip)
    ask = advisor.pick_question(s, kind)
    if ask:
        s.pending_question = {"field": ask["field"], "text": ask["text"], "turn": s.turn_no}
        s.asked_fields.append(ask["field"])
        s.last_question_turn = s.turn_no
    chips = advisor.merge_chips(chips, advisor.followups(kind, s, fact))

    kind = "adjust" if kind == "apply_change" else kind  # applied the same way as a typed adjustment
    ctx = None
    if kind in NARRATED_KINDS and s.profile_id:
        ctx = s.narration_ctx(kind, text, fact, focus=(u.parsed.mentioned if u.parsed and kind == "explain_district" else []),
                              mentioned=u.districts, leader_before=prev_leader_name, tip=tip,
                              notes=list(s.business_notes.values()),
                              needs=[n["need"] for n in db.industry_needs(s.profile_id)])
    kind = "suggest_preset" if kind == "suggest_general" else kind  # §7.2 rule 7b is a suggestion too
    bot = s.add_turn("assistant", reply, kind, fact, ctx)
    bot.interpret = interp
    return {
        "turn_id": bot.turn_id, "user_turn_id": user_turn.turn_id, "kind": kind, "reply_md": reply, "chips": chips,
        "state": s.public(), "facts": fact, "understood": u.public(),
        "question": s.pending_question,
        "narration": {"available": bool(narration_ready and ctx is not None)},
        "interpret": {"available": bool(narration_ready and interp), "mode": interp["mode"] if interp else None},
        "timing_ms": {"route": round(route_ms, 2), "total": round((time.perf_counter() - t0) * 1000, 2)},
    }


def answer_turn(s: Session, f: str, label: str) -> tuple[str, list[dict], dict]:
    """An answer to the advisor's question as a preview with Apply / Keep chips (§18.2). Records the
    business note; never changes weights."""
    changes = {f: label}
    s.business_notes[f] = advisor.note_for(f, label)
    p = advisor.preview(s, changes)
    s.pending_change, s.pending_suggest, s.pending_question = {"modifiers": changes}, None, None
    return advisor.preview_text(f, label, p), [APPLY_CHANGE_CHIP, KEEP_CHIP], {"preview": p, "changes": changes}


def _interpret_request(s: Session, kind: str, u: Understanding, text: str, question: dict | None) -> dict | None:
    """When the rules could not read a message, the request the LLM gets (§18.1), else None.
    Never for a confident rule result, an out-of-scope refusal or an unsupported/ambiguous industry."""
    n = len(u.tokens)
    if not n or n > INTERPRET_MAX_TOKENS:
        return None
    gave_up = kind in ("chat", "help", "clarify", "clarify_modifier", "unread_answer") and not u.districts
    # "our machines run three shifts" is about power, not a machining plant: with a question open, an
    # industry word without any switch cue or industry verb is read as an answer
    if question and kind == "suggest_preset" and len(u.tokens) > 1 and not set(u.tokens) & SWITCH_CUES:
        gave_up = True
    norm = nlp.normalize(text).strip()
    asking = norm.endswith("?") or NOT_ANSWER_RE.match(norm) or SHOW_ALL_RE.search(norm)
    if question and not question.get("reasked") and gave_up and not asking:
        return {"mode": "answer", "field": question["field"], "question": question["text"], "text": text}
    r = u.resolution
    if kind == "clarify" and r is not None and r.status in ("none", "low"):
        return {"mode": "industry", "text": text, "modifiers": u.parsed.as_map() if u.parsed else {}}
    if kind == "suggest_general":
        return {"mode": "industry", "text": text, "modifiers": u.parsed.as_map() if u.parsed else {}}
    if kind == "help" and u.fallback and n >= 3:
        return {"mode": "industry", "text": text, "modifiers": {}}
    # with an industry already on, an unreadable description falls through to the chat summary
    # ("washing powder and dish bars"): let the LLM try, unless it is a question or a request
    if kind == "chat" and n >= 3 and not u.districts and not asking and r is not None and r.status in ("none", "low")             and not (u.parsed and u.parsed.mentioned):
        return {"mode": "industry", "text": text, "modifiers": {}}
    return None


def preset_ids() -> list[str]:
    """All 12 presets in spec order (§6.1), for chips when nothing narrower is known."""
    return [p["id"] for p in db.profiles() if p["kind"] == "preset"]


def _clarify(r: intent.Resolution) -> tuple[str, list[dict], dict]:
    if r.status == "ambiguous":
        cands = r.candidates
        names = [templates.profile_name(p) for p in cands]
        reply = templates.clarify_ambiguous(r.term or "That", names)
    elif r.status == "unsupported":
        cands = intent.unsupported_chips(r.term)
        names = [templates.profile_name(p) for p in cands]
        reply = templates.clarify_unsupported(r.term, r.resource, names)
    elif r.status == "out_of_scope":
        cands = preset_ids()
        names = [templates.profile_name(p) for p in cands]
        reply = templates.clarify_out_of_scope(r.term)
    else:
        cands = r.candidates or preset_ids()
        names = [templates.profile_name(p) for p in cands]
        reply = templates.clarify_unknown(names, bool(r.candidates))
    pending = {"reason": r.status, "term": r.term, "candidates": cands}
    return reply, templates.chips(names) + ([templates.PICK_INDUSTRY] if not r.candidates and r.status not in
                                            ("ambiguous", "unsupported") else []), pending


def ensure_ranking(s: Session) -> str:
    """Compare/explain need a ranking. With no industry yet they use General Manufacturing and say
    so in the reply (never silently)."""
    if s.profile_id:
        return ""
    s.set_profile(intent.GENERAL)
    return f" (No industry picked yet, so this uses {s.profile_name}.)"


def _eligible_ids(s: Session) -> list[int]:
    return [e["district_id"] for e in sorted((e for e in s.ranking if e["eligible"]), key=lambda e: e["rank"])]


def _top(s: Session, n: int = 3) -> list[dict]:
    return [{k: e[k] for k in ("district_id", "name", "rank", "score", "band", "confidence")}
            for e in sorted((e for e in s.ranking if e["eligible"]), key=lambda e: e["rank"])[:n]]
