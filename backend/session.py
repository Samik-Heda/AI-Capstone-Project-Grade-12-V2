"""Server-side conversation sessions (spec §7.1): in memory, 2-hour TTL.

The frontend mirrors `Session.public()`; it never sends industry keys or weights through chat."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from . import modifiers as mod
from . import scoring

TTL_S = 2 * 3600
HISTORY_KEEP = 12


@dataclass
class Turn:
    turn_id: str
    role: str            # user | assistant
    text: str
    kind: str | None
    ts: float
    facts: dict | None = None
    ctx: dict | None = None        # narration context (kind, question, ranking/weights snapshot); not public
    narration: dict | None = None  # {text, grounding, model} once /narrate completes
    interpret: dict | None = None  # §18.1 request for the LLM fallback ({mode, text, ...}); not public
    narration_detail: dict | None = None  # §19.1 the 7B "Tell me more" answer


@dataclass
class Session:
    id: str
    profile_id: str | None = None
    profile_name: str | None = None
    profile_kind: str | None = None
    base_weights: dict[str, float] = field(default_factory=dict)
    modifiers: dict[str, str] = field(default_factory=dict)
    slider_overrides: dict[str, float] = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    ranking: list[dict] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    pinned: list[int] = field(default_factory=list)
    focus_district: int | None = None
    history: list[Turn] = field(default_factory=list)
    pending_clarify: dict | None = None
    pending_suggest: dict | None = None   # {preset_id, modifiers}: offered by an Apply chip, applied on click
    pending_save: dict | None = None      # {suggested}: waiting for a name after "save this"
    # §18.2 advisor
    pending_question: dict | None = None  # {field, text, turn}: the advisor's open question
    pending_change: dict | None = None    # {modifiers}: a previewed change, applied by its Apply chip
    asked_fields: list[str] = field(default_factory=list)
    business_notes: dict[str, str] = field(default_factory=dict)  # field -> what the user told us, for the brief
    turn_no: int = 0                      # user messages so far
    brief_cache: dict | None = None       # §18.3 last written brief: {key, sections, model}
    last_question_turn: int | None = None
    touched: float = field(default_factory=time.time)

    # ---------- state changes ----------

    def set_profile(self, profile_id: str, apply_pending: bool = False) -> None:
        """Load a profile; clears modifiers and slider overrides (§11). With `apply_pending`, the
        modifiers offered with an Apply chip are applied on top, but only when this is the profile
        that was suggested (picking a different one starts clean). The suggestion is consumed."""
        prof = scoring.load_profile(profile_id)
        if prof is None:
            raise KeyError(profile_id)
        self.profile_id, self.profile_name, self.profile_kind = prof["id"], prof["name"], prof["kind"]
        self.base_weights = scoring.normalize_weights(prof["weights"])
        self.constraints = scoring.normalize_constraints(prof["constraints"])
        self.modifiers, self.slider_overrides = {}, {}
        self.pending_change = None
        sug = self.pending_suggest or {}
        pending = sug.get("modifiers") if apply_pending and sug.get("preset_id") == prof["id"] else None
        self.pending_suggest = None
        if pending:
            self.modifiers = dict(pending)
        self.recompute()

    def apply_modifiers(self, changes: dict[str, str]) -> None:
        """Latest label per field wins; a chat modifier replaces a slider override on that field."""
        for f, lab in changes.items():
            self.modifiers[f] = lab
            self.slider_overrides.pop(f, None)
        self.recompute()

    def set_overrides(self, overrides: dict[str, float], constraints: dict | None = None) -> None:
        """Slider writes are absolute and replace any modifier on that field."""
        for f, w in overrides.items():
            self.slider_overrides[f] = float(w)
            self.modifiers.pop(f, None)
        if constraints is not None:
            self.constraints = scoring.normalize_constraints({**self.constraints, **constraints})
        self.recompute()

    def reset(self) -> None:
        if not self.profile_id:
            return
        if scoring.load_profile(self.profile_id) is not None:
            self.set_profile(self.profile_id)
        else:  # the user profile was deleted meanwhile: back to the base weights this session holds
            self.modifiers, self.slider_overrides = {}, {}
            self.recompute()

    def recompute(self) -> None:
        if not self.profile_id:
            return
        self.weights = mod.apply(self.base_weights, self.modifiers, self.slider_overrides)
        res = scoring.rank(self.weights, self.constraints)
        self.weights, self.ranking, self.excluded = res["weights"], res["ranking"], res["excluded"]

    def add_turn(self, role: str, text: str, kind: str | None = None, facts: dict | None = None,
                 ctx: dict | None = None) -> Turn:
        t = Turn(uuid.uuid4().hex[:12], role, text, kind, time.time(), facts, ctx)
        self.history = (self.history + [t])[-HISTORY_KEEP:]
        return t

    def find_turn(self, turn_id: str) -> Turn | None:
        return next((t for t in self.history if t.turn_id == turn_id), None)

    def narration_ctx(self, kind: str, question: str | None, facts: dict | None, **extra) -> dict:
        """Snapshot for narration. ranking/weights are replaced (never mutated) on recompute, so
        holding references is a consistent snapshot of this turn."""
        return {"kind": kind, "question": question, "facts": facts, "profile": self.profile_name,
                "ranking": self.ranking, "weights": self.weights, "excluded": self.excluded, **extra}

    # ---------- views ----------

    def entry(self, district_id: int) -> dict | None:
        return next((e for e in self.ranking if e["district_id"] == district_id), None)

    def leader(self) -> dict | None:
        return next((e for e in self.ranking if e["rank"] == 1), None)

    def public(self) -> dict:
        return {
            "session_id": self.id,
            "profile": ({"id": self.profile_id, "name": self.profile_name, "kind": self.profile_kind}
                        if self.profile_id else None),
            "base_weights": self.base_weights, "weights": self.weights,
            "modifiers": self.modifiers, "slider_overrides": self.slider_overrides,
            "constraints": self.constraints, "ranking": self.ranking, "excluded": self.excluded,
            "pinned": self.pinned, "focus_district": self.focus_district,
            "pending_clarify": self.pending_clarify, "pending_suggest": self.pending_suggest,
            "pending_save": self.pending_save,
            "pending_question": self.pending_question, "pending_change": self.pending_change,
            "business_notes": self.business_notes,
        }


class SessionStore:
    def __init__(self, ttl_s: float = TTL_S) -> None:
        self.ttl = ttl_s
        self._d: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for k in [k for k, s in self._d.items() if now - s.touched > self.ttl]:
            del self._d[k]

    def create(self) -> Session:
        now = time.time()
        s = Session(id=uuid.uuid4().hex)
        with self._lock:
            self._purge(now)
            self._d[s.id] = s
        return s

    def get(self, sid: str) -> Session | None:
        now = time.time()
        with self._lock:
            self._purge(now)
            s = self._d.get(sid)
            if s:
                s.touched = now
            return s

    def rename_profile(self, profile_id: str, name: str) -> None:
        """A user profile was renamed: sessions using it show the new name."""
        with self._lock:
            for s in self._d.values():
                if s.profile_id == profile_id:
                    s.profile_name = name

    def __len__(self) -> int:
        return len(self._d)


STORE = SessionStore()
