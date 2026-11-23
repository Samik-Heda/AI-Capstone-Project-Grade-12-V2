"""Phase 2: /api/rank and /api/district contract (spec §11)."""
import time

import pytest

from backend import facts, scoring


def test_rank_basic(client):
    r = client.post("/api/rank", json={"weights": {"power": 2, "water": 2}})
    assert r.status_code == 200
    body = r.json()
    assert len(body["ranking"]) == 36
    assert body["weights"]["power"] == pytest.approx(0.5)
    e = body["ranking"][0]
    assert set(e) >= {"district_id", "name", "region", "eligible", "reason", "rank", "score", "band",
                      "stability", "confidence", "contributions"}
    assert {x["id"] for x in body["excluded"]} == {1, 2}  # industrial land required by default


def test_rank_constraints_off(client):
    body = client.post("/api/rank", json={"weights": {"power": 1},
                                          "constraints": {"requires_industrial_land": False}}).json()
    assert all(e["eligible"] for e in body["ranking"])


@pytest.mark.parametrize("payload", [
    '{"weights": {"power": -1}}',
    '{"weights": {"power": NaN}}',
    '{"weights": {"power": Infinity}}',
    '{"weights": {"power": 0, "water": 0}}',
    '{"weights": {}}',
    '{"weights": {"bogus": 1}}',
    '{"weights": {"power": "high"}}',
    '{"weights": {"power": 1}, "constraints": {"min_norm": {"water": 2}}}',
    '{"weights": {"power": 1}, "constraints": {"surprise": true}}',
    '{}',
])
def test_rank_invalid_is_422(client, payload):
    r = client.post("/api/rank", content=payload, headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"


def test_negative_weight_v1_regression(client):
    # v1 accepted negative weights and produced scores like 375/100.
    r = client.post("/api/rank", json={"weights": {"power": -3, "water": 1}})
    assert r.status_code == 422


def test_rank_p95_under_50ms(client):
    p = scoring.load_profile("preset:automobile")
    body = {"weights": p["weights"], "constraints": p["constraints"]}
    client.post("/api/rank", json=body)
    ts = []
    for _ in range(60):
        t = time.perf_counter()
        client.post("/api/rank", json=body)
        ts.append((time.perf_counter() - t) * 1000)
    assert sorted(ts)[int(0.95 * (len(ts) - 1))] < 50


def test_district_shape(client):
    r = client.get("/api/district/15", params={"profile_id": "preset:food_beverages"})
    assert r.status_code == 200
    b = r.json()
    assert b["district"]["name"] == "Sangli"
    assert len(b["values"]) == 13
    assert b["entry"]["district_id"] == 15 and b["eligible"] is True
    assert all(s["norm"] >= 0.6 for s in b["strengths"])
    assert b["why_not_first"]["first"]["district_id"] != 15
    # popup values equal the ranking entry (same computation)
    for v in b["values"]:
        assert v["points"] == pytest.approx(b["entry"]["contributions"][v["field"]], abs=0.01)


def test_district_metro_ineligible(client):
    b = client.get("/api/district/1", params={"profile_id": "preset:automobile"}).json()
    assert b["eligible"] is False and b["entry"]["rank"] is None
    assert "industrial land" in b["reason"]


def test_district_no_strengths_note(client):
    # Gadchiroli under food processing: no weighted field reaches norm 0.6.
    b = client.get("/api/district/34", params={"profile_id": "preset:food_beverages"}).json()
    assert b["strengths"] == [] and b["strengths_note"] == facts.NO_STRENGTHS


def test_district_404s(client):
    assert client.get("/api/district/99").status_code == 404
    assert client.get("/api/district/5", params={"profile_id": "preset:nope"}).status_code == 404
