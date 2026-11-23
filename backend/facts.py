"""Deterministic facts layer (spec §5.6). Templates and narration only ever read from here."""
from __future__ import annotations

from . import db
from .scoring import dataset

MIN_WEIGHT = 0.05
STRENGTH_NORM = 0.6
WEAKNESS_NORM = 0.4
NO_STRENGTHS = "No standout strengths for this industry"


# ---------- display formatting (registry `format`) ----------

def _land_label(v: float) -> str:
    if v < 0.25:
        return "expensive"
    if v < 0.5:
        return "moderate"
    if v < 0.7:
        return "affordable"
    return "cheap"


def format_value(field: dict, raw: float) -> str:
    fmt = field["format"]
    if fmt == "pct1":
        return f"{raw:.1f}%"
    if fmt == "pct0":
        return f"{raw:.0f}%"
    if fmt == "km_per_km2":
        return f"{raw:.1f} km/km²"
    if fmt == "category":
        return field["category_labels"][str(int(raw))]
    if fmt == "km":
        return f"{raw:.0f} km"
    if fmt == "index10":
        return f"{raw:.1f} / 10"
    if fmt == "tonnes":
        if raw >= 1e6:
            return f"{raw / 1e6:.1f} M t"
        if raw >= 1e3:
            return f"{raw / 1e3:.0f} k t"
        return f"{raw:.0f} t"
    if fmt == "crops":
        return f"{raw:.0f} crops"
    if fmt in ("count", "int"):
        return f"{raw:,.0f}"
    if fmt == "land":
        return f"{raw:.2f} ({_land_label(raw)})"
    raise ValueError(f"unknown format {fmt!r} for {field['key']}")


def _fields() -> dict[str, dict]:
    return {f["key"]: f for f in db.fields()}


def _item(i: int, key: str, weights: dict, **extra) -> dict:
    d, f = dataset(), _fields()[key]
    j = d.keys.index(key)
    raw, norm, w = float(d.raw[i, j]), float(d.norm[i, j]), weights[key]
    return {"field": key, "label": f["label"], "raw": raw, "display": format_value(f, raw),
            "norm": round(norm, 3), "weight": round(w, 4), "points": round(100 * w * norm, 2),
            "status": f["status"], **extra}


# ---------- strengths / weaknesses / gaps ----------

def strengths(district_id: int, weights: dict) -> list[dict]:
    """Fields with w ≥ 0.05 and norm ≥ 0.6, by contribution, top 3. Never a low value."""
    d = dataset()
    i = d.row(district_id)
    cand = [k for j, k in enumerate(d.keys) if weights[k] >= MIN_WEIGHT and d.norm[i, j] >= STRENGTH_NORM]
    items = [_item(i, k, weights) for k in cand]
    return sorted(items, key=lambda x: (-x["points"], x["field"]))[:3]


def weaknesses(district_id: int, weights: dict) -> list[dict]:
    """Fields with w ≥ 0.05 and norm ≤ 0.4, by points lost, top 3."""
    d = dataset()
    i = d.row(district_id)
    out = []
    for j, k in enumerate(d.keys):
        if weights[k] >= MIN_WEIGHT and d.norm[i, j] <= WEAKNESS_NORM:
            out.append(_item(i, k, weights, lost=round(100 * weights[k] * (1 - d.norm[i, j]), 2)))
    return sorted(out, key=lambda x: (-x["lost"], x["field"]))[:3]


def why_not_first(district_id: int, ranking: list[dict], weights: dict) -> dict:
    """gap_f = 100·w_f·(norm_f(#1) − norm_f(d)); the gaps sum to the score difference."""
    d = dataset()
    first = next((e for e in ranking if e["rank"] == 1), None)
    if first is None:
        return {"first": None, "is_first": False, "total_gap": None, "behind": [], "ahead": []}
    me = next(e for e in ranking if e["district_id"] == district_id)
    i, t = d.row(district_id), d.row(first["district_id"])
    gaps = []
    for j, k in enumerate(d.keys):
        g = 100 * weights[k] * (d.norm[t, j] - d.norm[i, j])
        if weights[k] > 0 and abs(g) >= 0.005:
            f = _fields()[k]
            gaps.append({"field": k, "label": f["label"], "gap": round(float(g), 2),
                         "first_display": format_value(f, float(d.raw[t, j])),
                         "display": format_value(f, float(d.raw[i, j]))})
    total = sum(100 * weights[k] * (d.norm[t, j] - d.norm[i, j]) for j, k in enumerate(d.keys))
    return {
        "first": {"district_id": first["district_id"], "name": first["name"], "score": first["score"]},
        "is_first": me["rank"] == 1,
        "total_gap": round(float(total), 2),
        "behind": sorted((g for g in gaps if g["gap"] > 0), key=lambda g: -g["gap"])[:3],
        "ahead": sorted((g for g in gaps if g["gap"] < 0), key=lambda g: g["gap"])[:3],
    }


def compare(a_id: int, b_id: int, ranking: list[dict], weights: dict) -> dict:
    """Side-by-side rows. `leader` per field is decided on norm (direction-aware), never by the LLM."""
    d = dataset()
    by_id = {e["district_id"]: e for e in ranking}
    ia, ib = d.row(a_id), d.row(b_id)
    rows = []
    for j, k in enumerate(d.keys):
        f = _fields()[k]
        na, nb = float(d.norm[ia, j]), float(d.norm[ib, j])
        leader = "tie" if abs(na - nb) < 1e-9 else ("a" if na > nb else "b")
        rows.append({
            "field": k, "label": f["label"], "weight": round(weights[k], 4), "status": f["status"],
            "a": {"raw": float(d.raw[ia, j]), "display": format_value(f, float(d.raw[ia, j])),
                  "norm": round(na, 3), "points": round(100 * weights[k] * na, 2)},
            "b": {"raw": float(d.raw[ib, j]), "display": format_value(f, float(d.raw[ib, j])),
                  "norm": round(nb, 3), "points": round(100 * weights[k] * nb, 2)},
            "leader": leader,
        })
    ea, eb = by_id[a_id], by_id[b_id]
    diff = ea["score"] - eb["score"]
    return {
        "a": _summary(ea), "b": _summary(eb), "rows": rows,
        "score_diff": round(diff, 2),
        "direction": "tie" if abs(diff) < 0.005 else ("a" if diff > 0 else "b"),
    }


def _summary(e: dict) -> dict:
    return {k: e[k] for k in ("district_id", "name", "region", "eligible", "reason", "rank", "score", "band", "confidence")}


def district_values(district_id: int, weights: dict) -> list[dict]:
    """All 13 fields, raw formatted per the registry, with weight and contribution."""
    d = dataset()
    i = d.row(district_id)
    return [_item(i, k, weights) for k in d.keys]
