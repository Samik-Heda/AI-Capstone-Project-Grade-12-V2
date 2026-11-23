"""Scoring engine (spec §5): weight validation, score, constraints, ranking and rank stability.

Everything here is deterministic. The Monte Carlo stability draws use a fixed seed, so the same
weights always give the same band and confidence.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from . import db

N_DRAWS = 300
ALPHA = 60.0
STABILITY_SEED = 7
INDUSTRIAL_LAND_REASON = "no industrial land"


class WeightError(ValueError):
    """Invalid weight vector (negative, NaN, unknown key or all zero)."""


@dataclass(frozen=True)
class Dataset:
    keys: list[str]                 # registry order, the column order of norm/raw
    ids: list[int]                  # district ids, the row order of norm/raw
    names: list[str]
    regions: list[str]
    metro: np.ndarray               # bool (36,)
    norm: np.ndarray                # (36, 13)
    raw: np.ndarray                 # (36, 13)

    def row(self, district_id: int) -> int:
        return self.ids.index(district_id)


@lru_cache(maxsize=1)
def dataset() -> Dataset:
    keys = db.field_keys()
    ds = db.districts()
    ids = [d["id"] for d in ds]
    norm = np.zeros((len(ids), len(keys)))
    raw = np.zeros_like(norm)
    with db.connect() as con:
        for r in con.execute("SELECT district_id, field, raw, norm FROM district_value"):
            i, j = ids.index(r["district_id"]), keys.index(r["field"])
            norm[i, j], raw[i, j] = r["norm"], r["raw"]
    return Dataset(keys, ids, [d["name"] for d in ds], [d["region"] for d in ds],
                   np.array([bool(d["metro_core"]) for d in ds]), norm, raw)


def reload() -> None:
    """Drop the cached matrix (call after a reseed)."""
    dataset.cache_clear()


# ---------- weights ----------

def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Validate and renormalize to sum 1. Missing fields are 0. Raises WeightError (→ HTTP 422)."""
    keys = db.field_keys()
    unknown = sorted(set(weights) - set(keys))
    if unknown:
        raise WeightError(f"unknown field(s): {', '.join(unknown)}")
    for k, v in weights.items():
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            raise WeightError(f"weight for {k} must be a number")
        if not math.isfinite(v):
            raise WeightError(f"weight for {k} must be finite")
        if v < 0:
            raise WeightError(f"weight for {k} must be ≥ 0")
    total = sum(weights.values())
    if total <= 0:
        raise WeightError("weights must not all be zero")
    return {k: float(weights.get(k, 0.0)) / total for k in keys}


def weight_vector(weights: dict[str, float]) -> np.ndarray:
    return np.array([weights.get(k, 0.0) for k in dataset().keys])


# ---------- constraints ----------

DEFAULT_CONSTRAINTS = {"requires_industrial_land": True, "min_norm": {}, "max_port_km": None}


def normalize_constraints(c: dict | None) -> dict:
    out = {**DEFAULT_CONSTRAINTS, "min_norm": {}}
    if c:
        out.update({k: v for k, v in c.items() if v is not None or k == "max_port_km"})
        out["min_norm"] = dict(out.get("min_norm") or {})
    keys = set(db.field_keys())
    bad = sorted(set(out["min_norm"]) - keys)
    if bad:
        raise WeightError(f"unknown constraint field(s): {', '.join(bad)}")
    for k, v in out["min_norm"].items():
        if not (isinstance(v, (int, float)) and 0 <= v <= 1):
            raise WeightError(f"min_norm for {k} must be in [0, 1]")
    mp = out["max_port_km"]
    if mp is not None and not (isinstance(mp, (int, float)) and math.isfinite(mp) and mp >= 0):
        raise WeightError("max_port_km must be ≥ 0 or null")
    return out


def eligibility(constraints: dict) -> list[str | None]:
    """Reason per district (None = eligible), in dataset row order."""
    d = dataset()
    reasons: list[list[str]] = [[] for _ in d.ids]
    if constraints["requires_industrial_land"]:
        for i in np.flatnonzero(d.metro):
            reasons[i].append(INDUSTRIAL_LAND_REASON)
    fields = {f["key"]: f for f in db.fields()}
    from .facts import format_value  # facts imports this module
    for k, floor in constraints["min_norm"].items():
        j = d.keys.index(k)
        for i in np.flatnonzero(d.norm[:, j] < floor):
            # The district's own value, not two rounded norms ("0.25 < 0.25" read as a contradiction).
            shown = format_value(fields[k], float(d.raw[i, j]))
            reasons[i].append(f"{fields[k]['label'].lower()} below the minimum ({shown})")
    if constraints["max_port_km"] is not None:
        j = d.keys.index("port")
        for i in np.flatnonzero(d.raw[:, j] > constraints["max_port_km"]):
            reasons[i].append(f"port {d.raw[i, j]:.0f} km > {constraints['max_port_km']:.0f} km")
    return ["; ".join(r) if r else None for r in reasons]


# ---------- ranking ----------

def _base_order(w: np.ndarray, eligible_rows: np.ndarray) -> list[int]:
    """Eligible rows sorted by score, then top-3-weighted-field contributions, then name (§5.2)."""
    d = dataset()
    contrib = d.norm * w
    scores = contrib.sum(axis=1)
    top3 = np.argsort(-w, kind="stable")[:3]
    tie = contrib[:, top3].sum(axis=1)
    return sorted(eligible_rows.tolist(), key=lambda i: (-round(scores[i], 12), -round(tie[i], 12), d.names[i]))


def stability(w: np.ndarray, eligible_rows: np.ndarray, base_rank: np.ndarray) -> dict:
    """Monte Carlo rank stability (§5.5), vectorized: (n×13) @ (13×300).

    Draws w' ~ Dirichlet(α·w + 0.01). The band is the 10th–90th percentile rank, widened if needed
    to include the deterministic rank, so it always reads as "rank r (likely lo–hi)".
    """
    rng = np.random.default_rng(STABILITY_SEED)
    g = rng.standard_gamma(ALPHA * w + 0.01, size=(N_DRAWS, w.size))
    W = (g / g.sum(axis=1, keepdims=True)).T                     # 13 × 300
    S = dataset().norm[eligible_rows] @ W                        # n × 300
    n = len(eligible_rows)
    order = np.argsort(-S, axis=0, kind="stable")
    ranks = np.empty_like(order)
    ranks[order, np.arange(N_DRAWS)] = np.arange(1, n + 1)[:, None]
    lo = np.floor(np.percentile(ranks, 10, axis=1)).astype(int)
    hi = np.ceil(np.percentile(ranks, 90, axis=1)).astype(int)
    lo, hi = np.minimum(lo, base_rank), np.maximum(hi, base_rank)
    stab = (np.abs(ranks - base_rank[:, None]) <= 1).mean(axis=1)
    return {"lo": lo, "hi": hi, "stability": stab}


def confidence_label(lo: int, hi: int, stab: float) -> str:
    width = hi - lo
    if width <= 2 and stab >= 0.6:
        return "High"
    if width <= 5:
        return "Medium"
    return "Low"


def rank(weights: dict[str, float], constraints: dict | None = None, with_stability: bool = True) -> dict:
    """Score and rank all 36 districts. `weights` is validated and renormalized here."""
    weights = normalize_weights(weights)
    constraints = normalize_constraints(constraints)
    d = dataset()
    w = weight_vector(weights)
    contrib = 100 * d.norm * w
    scores = contrib.sum(axis=1)
    reasons = eligibility(constraints)
    eligible_rows = np.array([i for i, r in enumerate(reasons) if r is None], dtype=int)
    order = _base_order(w, eligible_rows)
    rank_of = {row: k + 1 for k, row in enumerate(order)}

    st = None
    if with_stability and len(order):
        rows = np.array(order)
        st = stability(w, rows, np.arange(1, len(rows) + 1))
        st = {row: (int(st["lo"][k]), int(st["hi"][k]), float(st["stability"][k])) for k, row in enumerate(order)}

    entries = []
    for i in order + sorted((i for i, r in enumerate(reasons) if r), key=lambda i: d.names[i]):
        e = {
            "district_id": d.ids[i], "name": d.names[i], "region": d.regions[i],
            "eligible": reasons[i] is None, "reason": reasons[i],
            "rank": rank_of.get(i), "score": round(float(scores[i]), 2),
            "band": None, "stability": None, "confidence": None,
            "contributions": {k: round(float(contrib[i, j]), 2) for j, k in enumerate(d.keys)},
        }
        if st and i in st:
            lo, hi, s = st[i]
            e.update(band=[lo, hi], stability=round(s, 3), confidence=confidence_label(lo, hi, s))
        entries.append(e)
    excluded = [{"id": e["district_id"], "name": e["name"], "reason": e["reason"]} for e in entries if not e["eligible"]]
    return {"ranking": entries, "weights": weights, "constraints": constraints, "excluded": excluded}


# ---------- profiles ----------

def load_profile(profile_id: str) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT * FROM profile WHERE id = ?", (profile_id,)).fetchone()
    if not r:
        return None
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "rationale": r["rationale"],
            "weights": json.loads(r["weights"]), "constraints": json.loads(r["constraints"])}
