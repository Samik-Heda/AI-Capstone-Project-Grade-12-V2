"""Phase 2: scoring engine, constraints, stability and facts (spec §5)."""
import json
import math
import time

import numpy as np
import pytest

from backend import db, facts, scoring

STD = ["automobile", "electronics", "general_manufacturing", "textile", "sugar_ethanol", "dairy", "food_beverages",
       "agro_cold", "pharma", "chemicals", "plastics_rubber", "logistics"]


def std(key):
    return scoring.load_profile(f"preset:{key}")


# ---------- weights ----------

def test_renormalization_sums_to_one():
    w = scoring.normalize_weights({"power": 2, "water": 6})
    assert math.isclose(sum(w.values()), 1.0)
    assert w["power"] == 0.25 and w["water"] == 0.75
    assert set(w) == set(db.field_keys())


def test_scaling_weights_does_not_change_ranking():
    a = scoring.rank({"power": 1, "water": 3}, with_stability=False)["ranking"]
    b = scoring.rank({"power": 10, "water": 30}, with_stability=False)["ranking"]
    assert [e["district_id"] for e in a] == [e["district_id"] for e in b]
    assert [e["score"] for e in a] == [e["score"] for e in b]


@pytest.mark.parametrize("bad", [
    {"power": -0.1, "water": 1}, {"power": float("nan")}, {"power": float("inf")},
    {"bogus": 1.0}, {"power": 0, "water": 0}, {}, {"power": "x"}, {"power": True},
])
def test_invalid_weights_rejected(bad):
    with pytest.raises(scoring.WeightError):
        scoring.normalize_weights(bad)


# ---------- score and contributions ----------

def test_score_matches_formula():
    d = scoring.dataset()
    w = scoring.normalize_weights({"power": 0.5, "literacy": 0.3, "port": 0.2})
    res = scoring.rank(w, with_stability=False)
    for e in res["ranking"]:
        i = d.row(e["district_id"])
        expected = 100 * sum(w[k] * d.norm[i, j] for j, k in enumerate(d.keys))
        assert e["score"] == pytest.approx(expected, abs=0.01)
        assert sum(e["contributions"].values()) == pytest.approx(e["score"], abs=0.05)
        for j, k in enumerate(d.keys):
            assert e["contributions"][k] == pytest.approx(100 * w[k] * d.norm[i, j], abs=0.006)


def test_single_field_score_is_100_times_norm():
    d = scoring.dataset()
    j = d.keys.index("water")
    for e in scoring.rank({"water": 1}, with_stability=False)["ranking"]:
        assert e["score"] == pytest.approx(100 * d.norm[d.row(e["district_id"]), j], abs=0.006)


def test_score_range():
    for e in scoring.rank({k: 1 for k in db.field_keys()})["ranking"]:
        assert 0 <= e["score"] <= 100


# ---------- ranking shape ----------

@pytest.mark.parametrize("key", STD)
def test_every_response_has_36_entries(key):
    p = std(key)
    res = scoring.rank(p["weights"], p["constraints"])
    assert len(res["ranking"]) == 36
    assert len({e["district_id"] for e in res["ranking"]}) == 36
    elig = [e for e in res["ranking"] if e["eligible"]]
    assert [e["rank"] for e in elig] == list(range(1, len(elig) + 1))
    assert all(a["score"] >= b["score"] for a, b in zip(elig, elig[1:]))
    for e in res["ranking"]:
        if not e["eligible"]:
            assert e["rank"] is None and e["reason"] and e["band"] is None


@pytest.mark.parametrize("key", STD)
def test_metro_ineligible_for_standard_profiles(key):
    p = std(key)
    assert p["constraints"]["requires_industrial_land"] is True
    res = scoring.rank(p["weights"], p["constraints"])
    for e in res["ranking"]:
        if e["district_id"] in (1, 2):
            assert not e["eligible"] and e["rank"] is None
            assert scoring.INDUSTRIAL_LAND_REASON in e["reason"]


def test_standard_profiles_weights_valid():
    for key in STD:
        w = std(key)["weights"]
        assert set(w) <= set(db.field_keys())
        assert sum(w.values()) == pytest.approx(1.0)


def test_industrial_land_toggle_makes_metro_eligible():
    res = scoring.rank({"power": 1}, {"requires_industrial_land": False})
    assert all(e["eligible"] for e in res["ranking"])
    assert res["excluded"] == []


def test_min_norm_constraint():
    d = scoring.dataset()
    j = d.keys.index("irrigation")
    res = scoring.rank({"power": 1}, {"requires_industrial_land": False, "min_norm": {"irrigation": 0.5}})
    for e in res["ranking"]:
        assert e["eligible"] == (d.norm[d.row(e["district_id"]), j] >= 0.5)
        if not e["eligible"]:
            assert "irrigation coverage below the minimum (" in e["reason"]


def test_max_port_km_constraint():
    d = scoring.dataset()
    j = d.keys.index("port")
    res = scoring.rank({"power": 1}, {"requires_industrial_land": False, "max_port_km": 150})
    for e in res["ranking"]:
        assert e["eligible"] == (d.raw[d.row(e["district_id"]), j] <= 150)


@pytest.mark.parametrize("bad", [{"min_norm": {"bogus": 0.2}}, {"min_norm": {"water": 1.5}}, {"max_port_km": -1}])
def test_invalid_constraints_rejected(bad):
    with pytest.raises(scoring.WeightError):
        scoring.normalize_constraints(bad)


def test_tie_break_by_top3_then_name():
    # Equal-score ties among districts sharing every norm on the used field; name decides.
    res = scoring.rank({"highway": 1}, {"requires_industrial_land": False}, with_stability=False)["ranking"]
    top = [e for e in res if e["score"] == res[0]["score"]]
    assert len(top) > 1
    assert [e["name"] for e in top] == sorted(e["name"] for e in top)


# ---------- stability ----------

def test_stability_deterministic_and_band_contains_rank():
    p = std("food_beverages")
    a = scoring.rank(p["weights"], p["constraints"])["ranking"]
    b = scoring.rank(p["weights"], p["constraints"])["ranking"]
    assert a == b
    for e in a:
        if e["eligible"]:
            lo, hi = e["band"]
            assert lo <= e["rank"] <= hi
            assert 0 <= e["stability"] <= 1
            assert e["confidence"] in {"High", "Medium", "Low"}


def test_confidence_labels():
    assert scoring.confidence_label(3, 5, 0.6) == "High"
    assert scoring.confidence_label(3, 5, 0.59) == "Medium"
    assert scoring.confidence_label(1, 6, 0.9) == "Medium"
    assert scoring.confidence_label(1, 7, 0.9) == "Low"


def test_stability_under_30ms():
    p = std("general_manufacturing")
    w = scoring.weight_vector(scoring.normalize_weights(p["weights"]))
    rows = np.arange(2, 36)
    base = np.arange(1, 35)
    scoring.stability(w, rows, base)
    ts = []
    for _ in range(30):
        t = time.perf_counter()
        scoring.stability(w, rows, base)
        ts.append((time.perf_counter() - t) * 1000)
    assert sorted(ts)[len(ts) // 2] < 30


# ---------- facts ----------

@pytest.mark.parametrize("key", STD)
def test_strengths_never_low_and_weaknesses_never_high(key):
    p = std(key)
    w = scoring.normalize_weights(p["weights"])
    for did in range(1, 37):
        for s in facts.strengths(did, w):
            assert s["norm"] >= 0.6 and s["weight"] >= 0.05
        for x in facts.weaknesses(did, w):
            assert x["norm"] <= 0.4 and x["weight"] >= 0.05
        assert len(facts.strengths(did, w)) <= 3 and len(facts.weaknesses(did, w)) <= 3


def test_why_not_first_gaps_sum_to_score_difference():
    p = std("food_beverages")
    res = scoring.rank(p["weights"], p["constraints"])
    w = res["weights"]
    for e in res["ranking"]:
        wnf = facts.why_not_first(e["district_id"], res["ranking"], w)
        assert wnf["total_gap"] == pytest.approx(wnf["first"]["score"] - e["score"], abs=0.02)
        assert all(g["gap"] > 0 for g in wnf["behind"]) and all(g["gap"] < 0 for g in wnf["ahead"])
        if e["rank"] == 1:
            assert wnf["is_first"] and wnf["behind"] == []


def test_compare_direction_from_scores():
    p = std("food_beverages")
    res = scoring.rank(p["weights"], p["constraints"])
    c = facts.compare(8, 13, res["ranking"], res["weights"])
    by = {e["district_id"]: e for e in res["ranking"]}
    assert c["direction"] == ("a" if by[8]["score"] > by[13]["score"] else "b")
    assert len(c["rows"]) == 13
    for r in c["rows"]:
        assert r["leader"] == ("tie" if r["a"]["norm"] == r["b"]["norm"] else "a" if r["a"]["norm"] > r["b"]["norm"] else "b")


def test_format_value_covers_every_registry_field():
    d = scoring.dataset()
    for j, f in enumerate(db.fields()):
        for i in range(36):
            assert facts.format_value(f, float(d.raw[i, j]))
    pop = db.registry()["display_only"][0]
    assert facts.format_value(pop, 3085411) == "3,085,411"
