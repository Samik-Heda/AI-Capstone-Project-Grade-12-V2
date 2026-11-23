"""Phase 1: field registry, transforms, normalization, seeded DB and map asset."""
import csv
import json
import math
import sqlite3

import pytest

from backend import config, db
from pipeline import seed

FIELDS = {f["key"]: f for f in db.fields()}


# ---------- registry ----------

def test_registry_has_13_fields_with_status():
    assert list(FIELDS) == ["literacy", "road_density", "highway", "rail", "port", "power", "water",
                            "crop_output", "crop_diversity", "irrigation", "estates", "msme", "land"]
    for f in FIELDS.values():
        assert f["status"] in {"Sourced", "Derived", "Estimated"}
        assert f["direction"] in {"higher", "lower"}
        assert f["transform"] in {"linear", "log1p", "categorical"}
        for k in ("label", "short", "source", "format", "synonyms", "explain_high", "explain_low"):
            assert f[k], (f["key"], k)


def test_registry_statuses_match_spec():
    assert FIELDS["literacy"]["status"] == "Sourced"
    assert FIELDS["port"]["status"] == "Derived"
    assert all(f["status"] == "Estimated" for k, f in FIELDS.items() if k not in ("literacy", "port"))
    assert FIELDS["port"]["direction"] == "lower"


def test_raw_columns_exist_in_csv():
    with open(config.DATA_DIR / "districts_raw.csv", newline="") as f:
        cols = next(csv.reader(f))
    for f in FIELDS.values():
        if f["key"] != "port":
            assert f["raw_column"] in cols


# ---------- transforms ----------

def test_transform_linear_and_log():
    assert seed.apply_transform(FIELDS["literacy"], 86.0) == 86.0
    assert seed.apply_transform(FIELDS["msme"], 45000) == pytest.approx(math.log1p(45000))


def test_transform_categorical():
    hw = FIELDS["highway"]
    assert [seed.apply_transform(hw, x) for x in (0, 1, 2)] == [0.0, 0.5, 1.0]
    with pytest.raises(ValueError):
        seed.apply_transform(hw, 3)


def test_haversine_known_distance():
    # Mumbai Port -> JNPT is ~11 km across the harbour
    assert seed.haversine_km(18.9543, 72.8442, 18.9489, 72.9515) == pytest.approx(11.3, abs=0.3)


# ---------- normalization ----------

def test_normalize_fits_on_non_metro_and_clips():
    f = {"key": "x", "transform": "linear", "direction": "higher"}
    raw = [1000.0, 900.0] + [float(i) for i in range(1, 35)]
    metro = [True, True] + [False] * 34
    norm, params = seed.normalize_field(f, raw, metro)
    assert params["hi"] < 100  # metro outliers did not set the scale
    assert norm[0] == norm[1] == 1.0
    assert all(0.0 <= n <= 1.0 for n in norm)


def test_normalize_lower_is_better_inverts():
    f = {"key": "x", "transform": "linear", "direction": "lower"}
    raw = [float(i) for i in range(36)]
    norm, _ = seed.normalize_field(f, raw, [False] * 36)
    assert norm[0] == 1.0 and norm[-1] == 0.0


def test_normalize_degenerate_scale_raises():
    f = {"key": "x", "transform": "linear", "direction": "higher"}
    with pytest.raises(ValueError):
        seed.normalize_field(f, [5.0] * 36, [False] * 36)


def test_impute_regional_median():
    rows = [{"id": 1, "region": "A", "c": ""}, {"id": 2, "region": "A", "c": "2"},
            {"id": 3, "region": "A", "c": "4"}, {"id": 4, "region": "B", "c": "100"}]
    assert seed.impute_regional_median(rows, "c") == [1]
    assert rows[0]["c"] == 3.0


# ---------- seeded DB ----------

@pytest.fixture(scope="module")
def con():
    c = sqlite3.connect(config.path("db"))
    yield c
    c.close()


def test_36_districts_seeded(con):
    assert con.execute("SELECT COUNT(*) FROM district").fetchone()[0] == 36
    assert con.execute("SELECT COUNT(*) FROM district_value").fetchone()[0] == 36 * 13
    assert [r[0] for r in con.execute("SELECT id FROM district WHERE metro_core=1")] == [1, 2]


def test_all_norms_in_unit_interval(con):
    lo, hi = con.execute("SELECT MIN(norm), MAX(norm) FROM district_value").fetchone()
    assert 0.0 <= lo and hi <= 1.0


def test_port_distance_uses_polygon_centroids(con):
    assert con.execute("SELECT value FROM meta WHERE key='centroid_source'").fetchone()[0] == "polygon"
    with open(config.path("centroids_csv"), newline="") as f:
        cents = {int(r["id"]): (float(r["lat"]), float(r["lon"])) for r in csv.DictReader(f)}
    dbc = {r[0]: (r[1], r[2]) for r in con.execute("SELECT id, centroid_lat, centroid_lon FROM district")}
    assert cents == dbc
    # Ratnagiri's HQ sits on the coast (~1 km from its port); its polygon centroid must not.
    raw = dict(con.execute("SELECT district_id, raw FROM district_value WHERE field='port'").fetchall())
    assert raw[5] > 10


def test_raigad_port_score_at_least_ratnagiri(con):
    norm = dict(con.execute("SELECT district_id, norm FROM district_value WHERE field='port'").fetchall())
    assert norm[4] >= norm[5]


def test_user_profiles_survive_reseed(tmp_path):
    path = tmp_path / "gc.db"
    seed.build(path)
    c = sqlite3.connect(path)
    with c:
        c.execute("INSERT INTO profile (id, kind, name, weights) VALUES ('user:t', 'user', 'T', '{}')")
    c.close()
    seed.build(path)
    c = sqlite3.connect(path)
    assert c.execute("SELECT COUNT(*) FROM profile WHERE id='user:t'").fetchone()[0] == 1
    c.close()


# ---------- map asset ----------

@pytest.fixture(scope="module")
def map_json():
    return json.loads(config.path("map_json").read_text(encoding="utf-8"))


def test_map_has_36_unique_ids(map_json):
    ids = [d["id"] for d in map_json["districts"]]
    assert sorted(ids) == list(range(1, 37))
    assert len(set(ids)) == 36


def test_map_paths_and_labels_inside_viewbox(map_json):
    _, _, w, h = map(float, map_json["viewBox"].split())
    for d in map_json["districts"]:
        assert d["d"].startswith("M") and d["d"].endswith("Z")
        assert 0 <= d["label_x"] <= w and 0 <= d["label_y"] <= h

