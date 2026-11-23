"""Phase 7: profile builder draft (§9.1), user-profile CRUD round trips, save-from-chat, clusters (§10).
Runs without Ollama: the draft's rule tiers carry these cases (the semantic tier only adds)."""
import pytest

from backend import clusters, profiles, scoring


def draft(client, text, base=None):
    body = {"text": text} | ({"base_preset_id": base} if base else {})
    r = client.post("/api/profiles/draft", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_draft_contract(client):
    d = draft(client, "pharmaceutical plant needing clean water and skilled graduates; land cost does not matter")
    for k in ("base_preset_id", "preset_confidence", "modifiers", "weights", "constraints", "rationale_draft", "preview_top5"):
        assert k in d
    assert d["base_preset_id"] == "preset:pharma"
    assert abs(sum(d["weights"].values()) - 1) < 1e-9 and len(d["weights"]) == 13
    assert len(d["preview_top5"]) == 5 and all("name" in e for e in d["preview_top5"])
    assert d["rationale_draft"].startswith("Based on Pharmaceuticals")


def test_draft_weights_are_the_chat_function(client):
    """§9.1 step 4: the builder's weights are modifiers.apply on the preset, exactly as chat computes them."""
    from backend import modifiers as mod
    d = draft(client, "dairy needs a milk-rich farming region and water")
    base = scoring.normalize_weights(profiles.get(d["base_preset_id"])["weights"])
    want = scoring.normalize_weights(mod.apply(base, d["modifiers"]))
    assert all(abs(d["weights"][k] - want[k]) < 1e-9 for k in want)


def test_negated_field_never_rises(client):
    """'Land does not matter' gave land 64% in v1; now it ends below the starting preset."""
    d = draft(client, "Need a port for exports. Land cost does not matter.")
    assert d["modifiers"]["land"] == "low"
    assert d["weights"]["land"] < d["base_weights"]["land"]
    assert d["weights"]["port"] > d["base_weights"]["port"]


def test_ignore_means_zero(client):
    d = draft(client, "ignore literacy; we need cheap land and power")
    assert d["weights"]["literacy"] == 0


def test_no_industry_starts_from_general(client):
    d = draft(client, "heavy trucks need highways and good roads")
    assert d["base_preset_id"] == "preset:general_manufacturing" and d["preset_tier"] == "default"


def test_zero_base_field_gets_the_floor(client):
    """Presets are stored sparse: a factor absent from General Manufacturing still gets the 0.05 floor."""
    d = draft(client, "don't care about ports, focus on crops and crop variety")
    assert d["base_weights"]["crop_output"] == 0 and d["weights"]["crop_output"] > 0


def test_start_from_overrides_the_auto_pick(client):
    d = draft(client, "water is critical", base="preset:textile")
    assert d["base_preset_id"] == "preset:textile" and d["modifiers"] == {"water": "critical"}
    assert client.post("/api/profiles/draft", json={"text": "x", "base_preset_id": "preset:nope"}).status_code == 422
    assert client.post("/api/profiles/draft", json={"text": ""}).status_code == 422


def test_resolver_false_positives_fixed_in_p7():
    from backend import nlp
    find = lambda t: [(m.kind, m.values) for m in nlp.term_matcher().find(nlp.tokenize(t))]  # noqa: E731
    assert ("industry", ("preset:plastics_rubber",)) not in find("irrigated sugarcane belt")
    assert not any(k == "industry" for k, _ in find("heavy trucks need highways"))
    assert ("field", ("rail",)) not in find("trained technicians")
    assert ("industry", ("preset:automobile",)) in find("truck manufacturing plant")
    assert ("industry", ("preset:logistics",)) in find("a trucking company")


def test_crud_round_trip_with_generated_rationale(client):
    d = draft(client, "cold storage for vegetables near farms with reliable power")
    r = client.post("/api/profiles", json={"name": "Builder test", "weights": d["weights"], "constraints": d["constraints"],
                                           "base_profile_id": d["base_preset_id"],
                                           "provenance": {"source": "builder", "modifiers": d["modifiers"]}})
    assert r.status_code == 201
    p = r.json()["profile"]
    try:
        assert p["kind"] == "user" and p["rationale"].startswith("Based on Agro & Cold Storage")
        assert all(abs(p["weights"][k] - d["weights"][k]) < 1e-9 for k in d["weights"])
        assert any(x["id"] == p["id"] for x in client.get("/api/profiles").json()["user"])
        # use it in a session, rename it: the session shows the new name
        sid = client.post("/api/session").json()["session_id"]
        assert client.post(f"/api/session/{sid}/profile", json={"profile_id": p["id"]}).json()["state"]["profile"]["name"] == "Builder test"
        assert client.put(f"/api/profiles/{p['id']}", json={"name": "Builder renamed"}).json()["profile"]["name"] == "Builder renamed"
        assert client.get(f"/api/session/{sid}").json()["state"]["profile"]["name"] == "Builder renamed"
        # behaves like a preset: adjust, then reset returns exactly to the saved weights
        client.post("/api/message", json={"session_id": sid, "text": "water is critical"})
        st = client.post(f"/api/session/{sid}/reset").json()["state"]
        assert all(abs(st["weights"][k] - p["weights"][k]) < 1e-9 for k in p["weights"])
    finally:
        assert client.delete(f"/api/profiles/{p['id']}").status_code == 204
    assert client.delete(f"/api/profiles/{p['id']}").status_code == 404
    assert client.put("/api/profiles/preset:dairy", json={"name": "x"}).status_code == 403
    assert client.delete("/api/profiles/preset:dairy").status_code == 403


def test_create_rejects_unknown_base(client):
    w = {"power": 1}
    assert client.post("/api/profiles", json={"name": "x", "weights": w, "base_profile_id": "preset:nope"}).status_code == 422


def test_save_from_chat_rationale_and_round_trip(client):
    sid = client.post("/api/session").json()["session_id"]
    client.post(f"/api/session/{sid}/profile", json={"profile_id": "preset:agro_cold"})
    client.post("/api/message", json={"session_id": sid, "text": "don't care about water"})
    b = client.post("/api/message", json={"session_id": sid, "text": "save this as chat round trip"}).json()
    pid = b["state"]["profile"]["id"]
    try:
        p = next(x for x in client.get("/api/profiles").json()["user"] if x["id"] == pid)
        assert p["rationale"] == "Based on Agro & Cold Storage; less weight on water supply."
        st = client.post(f"/api/session/{sid}/reset").json()["state"]
        assert all(abs(st["weights"][k] - p["weights"][k]) < 1e-9 for k in p["weights"])
    finally:
        client.delete(f"/api/profiles/{pid}")


def test_rationale_grammar():
    r = profiles.rationale_for("preset:pharma", {"water": 0.3, "literacy": 0.3, "land": 0.0},
                               modifiers={"water": "critical", "literacy": "high", "land": "low"})
    assert r == "Based on Pharmaceuticals; most weight on water supply; more weight on skilled workers; ignores cheap land."


# ---------- clusters (§10) ----------

def test_clusters_contract(client):
    r = client.post("/api/clusters", json={})
    assert r.status_code == 200
    c = r.json()
    assert 3 <= c["k"] <= 6 and -1 <= c["silhouette"] <= 1
    assert c["silhouette"] == max(c["silhouettes"].values())  # k chosen by silhouette
    ids = sorted(i for cl in c["clusters"] for i in cl["district_ids"])
    assert ids == sorted(scoring.dataset().ids)  # every district exactly once
    labels = [cl["label"] for cl in c["clusters"]]
    assert len(set(labels)) == len(labels) and len({cl["name"] for cl in c["clusters"]}) == len(labels)
    assert len({cl["color"] for cl in c["clusters"]}) == c["k"]
    for cl in c["clusters"]:
        assert set(cl["centroid_z"]) == set(scoring.dataset().keys)
        assert ": high " in cl["label"] and "; low " in cl["label"]


@pytest.mark.parametrize("k", [3, 4, 5, 6])
def test_every_k_has_unique_labels_and_colors(client, k):
    c = client.post("/api/clusters", json={"k": k}).json()
    assert c["k"] == k
    assert len({cl["label"] for cl in c["clusters"]}) == k and len({cl["color"] for cl in c["clusters"]}) == k


def test_clusters_validation_and_cache(client):
    assert client.post("/api/clusters", json={"k": 2}).status_code == 422
    assert client.post("/api/clusters", json={"k": 7}).status_code == 422
    assert clusters.compute() is clusters.compute()
