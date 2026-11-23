"""Phase 3: the 12 presets and ground truth (spec §6)."""
import json
from pathlib import Path

import pytest

from backend import db, scoring
from eval.run_benchmarks import spec_start_weights

DATA = Path(__file__).resolve().parent.parent / "data"
SPEC = spec_start_weights()  # §6.1 starting weights; Phase 3 may tune each field only within ±0.05


def _load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


PRESET = _load("profiles_presets.json")


def test_one_list_of_twelve_in_spec_order():
    assert [p["key"] for p in PRESET] == list(SPEC) and len(PRESET) == 12
    assert not (DATA / "profiles_standard.json").exists()


@pytest.mark.parametrize("prof", PRESET, ids=lambda p: p["key"])
def test_weights_valid_and_sum_to_one(prof):
    keys = set(db.field_keys())
    assert set(prof["weights"]) <= keys
    assert all(v > 0 for v in prof["weights"].values())
    assert sum(prof["weights"].values()) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("prof", PRESET, ids=lambda p: p["key"])
def test_rationale_and_industrial_land(prof):
    assert len(prof["rationale"].strip()) >= 20
    assert prof["constraints"]["requires_industrial_land"] is True
    for field, floor in prof["constraints"].get("min_norm", {}).items():
        assert field in db.field_keys() and 0 < floor < 1


@pytest.mark.parametrize("prof", PRESET, ids=lambda p: p["key"])
def test_tuning_within_005_of_spec(prof):
    spec = SPEC[prof["key"]]
    for f in set(spec) | set(prof["weights"]):
        assert abs(prof["weights"].get(f, 0) - spec.get(f, 0)) <= 0.05 + 1e-9, f


def test_profiles_seeded():
    with db.connect() as con:
        rows = [(r["id"], r["kind"]) for r in con.execute("SELECT id, kind FROM profile WHERE kind != 'user' ORDER BY rowid")]
    assert rows == [(f"preset:{p['key']}", "preset") for p in PRESET]
    assert scoring.load_profile("preset:pharma")["weights"] == next(p for p in PRESET if p["key"] == "pharma")["weights"]


def test_ground_truth():
    gt = {k: v for k, v in _load("ground_truth.json").items() if not k.startswith("_")}
    names = {d["name"]: d for d in db.districts()}
    assert set(gt) == set(SPEC) - {"plastics_rubber"}  # Plastics & Rubber: face validity only
    for key, ds in gt.items():
        assert len(ds) >= 5 and len(set(ds)) == len(ds), key
        assert all(n in names and not names[n]["metro_core"] for n in ds), key


def test_registry_has_plain_labels():
    labels = [f["plain_label"] for f in db.fields()]
    assert len(labels) == 13 and all(labels) and len(set(labels)) == 13


def test_lexicon_and_exemplars_use_preset_ids():
    ids = {f"preset:{p['key']}" for p in PRESET}
    lx, ex = _load("lexicon_industry.json"), _load("intent_exemplars.json")
    assert set(lx["profiles"]) == ids and set(ex["profiles"]) == ids
    assert all(set(v) <= ids for v in lx["unsupported_nearest"].values())
    assert all(len(v) >= 25 for v in ex["profiles"].values())


def test_unsupported_list_has_resources():
    unsupported = _load("lexicon_industry.json")["unsupported"]
    assert {"cement", "steel", "mining"} <= set(unsupported)
    assert all(isinstance(v, str) and v for v in unsupported.values())


def test_profiles_endpoint_lists_presets_and_users(client):
    body = client.get("/api/profiles").json()
    assert set(body) == {"preset", "user"} and len(body["preset"]) == 12


def test_rank_by_preset_id(client):
    prof = scoring.load_profile("preset:logistics")
    body = client.post("/api/rank", json={"weights": prof["weights"], "constraints": prof["constraints"]}).json()
    assert len(body["ranking"]) == 36
    r = client.get("/api/district/4", params={"profile_id": "preset:logistics"})
    assert r.status_code == 200 and r.json()["profile"]["kind"] == "preset"
