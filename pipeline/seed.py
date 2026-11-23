"""Seed data/gc.db from the raw CSVs and field registry (spec §4.5, §4.6).

Usage: python pipeline/seed.py

Rebuilds everything except user profiles (kind='user') and the embedding cache
(a pure cache keyed by text hash + model), which both survive a reseed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import config  # noqa: E402

DATA = ROOT / "data"

SCHEMA = """
CREATE TABLE IF NOT EXISTS district (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, region TEXT NOT NULL,
  alt_names TEXT NOT NULL DEFAULT '[]',
  metro_core INTEGER NOT NULL DEFAULT 0,
  population INTEGER, centroid_lat REAL, centroid_lon REAL
);
CREATE TABLE IF NOT EXISTS district_value (
  district_id INTEGER REFERENCES district(id), field TEXT NOT NULL,
  raw REAL NOT NULL, norm REAL NOT NULL CHECK (norm BETWEEN 0 AND 1),
  PRIMARY KEY (district_id, field)
);
CREATE TABLE IF NOT EXISTS profile (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('preset','user')),
  name TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',
  weights TEXT NOT NULL,
  constraints TEXT NOT NULL DEFAULT '{}',
  provenance TEXT NOT NULL DEFAULT '{}',
  base_profile_id TEXT, description TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS embedding_cache (text_hash TEXT PRIMARY KEY, model TEXT, vector BLOB);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


# ---------- pure functions (unit-tested) ----------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def apply_transform(field: dict, raw: float) -> float:
    t = field["transform"]
    if t == "linear":
        return float(raw)
    if t == "log1p":
        return math.log1p(raw)
    if t == "categorical":
        key = str(int(raw))
        if key not in field["categories"]:
            raise ValueError(f"{field['key']}: category {raw!r} not in {list(field['categories'])}")
        return float(field["categories"][key])
    raise ValueError(f"unknown transform {t!r}")


def normalize_field(field: dict, raw: list[float], metro: list[bool]) -> tuple[list[float], dict]:
    """Transform, fit P5/P95 on non-metro districts, clip to [0,1], invert if lower-better."""
    v = np.array([apply_transform(field, x) for x in raw], dtype=float)
    if field["transform"] == "categorical":
        norm = v.copy()  # categorical map is already on [0,1]
        params = {"lo": 0.0, "hi": 1.0}
    else:
        fit = v[~np.array(metro)]
        lo, hi = float(np.percentile(fit, 5)), float(np.percentile(fit, 95))
        if hi <= lo:
            raise ValueError(f"{field['key']}: degenerate scale lo={lo} hi={hi}")
        norm = np.clip((v - lo) / (hi - lo), 0.0, 1.0)
        params = {"lo": lo, "hi": hi}
    if field["direction"] == "lower":
        norm = 1.0 - norm
    return [round(float(x), 6) for x in norm], params


def impute_regional_median(rows: list[dict], col: str) -> list[int]:
    """Fill blanks with the regional median; returns ids imputed (status becomes Estimated)."""
    imputed = []
    for r in rows:
        if r[col] in ("", None):
            peers = [float(p[col]) for p in rows if p["region"] == r["region"] and p[col] not in ("", None)]
            r[col] = statistics.median(peers)
            imputed.append(int(r["id"]))
    return imputed


# ---------- loaders ----------

def load_inputs():
    registry = json.loads((DATA / "fields.json").read_text(encoding="utf-8"))
    districts = json.loads((DATA / "districts.json").read_text(encoding="utf-8"))
    with open(DATA / "districts_raw.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with open(DATA / "ports.csv", newline="", encoding="utf-8") as f:
        ports = [{"name": p["name"], "lat": float(p["lat"]), "lon": float(p["lon"])} for p in csv.DictReader(f)]
    return registry, districts, rows, ports


def load_centroids(districts: list[dict]) -> tuple[dict[int, tuple[float, float]], str]:
    path = config.path("centroids_csv")
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            c = {int(r["id"]): (float(r["lat"]), float(r["lon"])) for r in csv.DictReader(f)}
        if sorted(c) == list(range(1, 37)):
            return c, "polygon"
    print("WARNING: centroids.csv missing/incomplete; falling back to HQ coordinates")
    return {d["id"]: tuple(d["hq"]) for d in districts}, "hq_fallback"


def dataset_hash() -> str:
    h = hashlib.sha256()
    for name in ["districts_raw.csv", "ports.csv", "fields.json", "districts.json", "centroids.csv"]:
        p = DATA / name
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


# ---------- main ----------

def build(db_path: Path | None = None) -> dict:
    registry, districts, rows, ports = load_inputs()
    fields = registry["fields"]
    by_id = {d["id"]: d for d in districts}

    if [int(r["id"]) for r in rows] != list(range(1, 37)):
        raise SystemExit("districts_raw.csv must have ids 1..36 in order")
    for r in rows:
        d = by_id[int(r["id"])]
        if r["name"] != d["name"] or r["region"] != d["region"]:
            raise SystemExit(f"id {r['id']}: CSV {r['name']}/{r['region']} != master {d['name']}/{d['region']}")

    imputed = {}
    for f in fields:
        if f["raw_column"]:
            ids = impute_regional_median(rows, f["raw_column"])
            if ids:
                imputed[f["key"]] = ids

    centroids, centroid_source = load_centroids(districts)
    port_km = {}
    nearest_port = {}
    for d in districts:
        lat, lon = centroids[d["id"]]
        dist, name = min((haversine_km(lat, lon, p["lat"], p["lon"]), p["name"]) for p in ports)
        port_km[d["id"]], nearest_port[d["id"]] = round(dist, 1), name

    metro = [bool(by_id[int(r["id"])]["metro_core"]) for r in rows]
    values: dict[str, tuple[list[float], list[float]]] = {}
    norm_params = {}
    for f in fields:
        raw = [port_km[int(r["id"])] for r in rows] if f["key"] == "port" else [float(r[f["raw_column"]]) for r in rows]
        norm, params = normalize_field(f, raw, metro)
        values[f["key"]] = (raw, norm)
        norm_params[f["key"]] = params

    db_path = db_path or config.path("db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        with con:
            con.execute("DELETE FROM district_value")
            con.execute("DELETE FROM district")
            con.execute("DELETE FROM profile WHERE kind != 'user'")
            con.execute("DELETE FROM meta")
            for r in rows:
                d = by_id[int(r["id"])]
                lat, lon = centroids[d["id"]]
                con.execute(
                    "INSERT INTO district VALUES (?,?,?,?,?,?,?,?)",
                    (d["id"], d["name"], d["region"], json.dumps(d["alt_names"]), d["metro_core"],
                     int(float(r["population"])), lat, lon),
                )
            for key, (raw, norm) in values.items():
                con.executemany(
                    "INSERT INTO district_value VALUES (?,?,?,?)",
                    [(int(r["id"]), key, raw[i], norm[i]) for i, r in enumerate(rows)],
                )
            seed_profiles(con)
            meta = {
                "normalization": json.dumps(norm_params),
                "build_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "dataset_hash": dataset_hash(),
                "centroid_source": centroid_source,
                "nearest_port": json.dumps(nearest_port),
                "imputed": json.dumps(imputed),
            }
            con.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
    finally:
        con.close()
    return {"db": str(db_path), "port_km": port_km, "values": values, "meta": meta}


def seed_profiles(con: sqlite3.Connection) -> None:
    """Load the 12 presets (spec §6.1). The old 8 standard + 10 preset split is retired."""
    p = DATA / "profiles_presets.json"
    if p.exists():
        kind, prefix = "preset", "preset"
        for prof in json.loads(p.read_text(encoding="utf-8")):
            con.execute(
                "INSERT INTO profile (id, kind, name, rationale, weights, constraints) VALUES (?,?,?,?,?,?)",
                (f"{prefix}:{prof['key']}", kind, prof["name"], prof.get("rationale", ""),
                 json.dumps(prof["weights"]), json.dumps(prof.get("constraints", {}))),
            )


if __name__ == "__main__":
    out = build()
    pk = out["port_km"]
    norm_port = dict(zip(range(1, 37), out["values"]["port"][1]))
    print(f"seeded {out['db']} (hash {out['meta']['dataset_hash']}, centroids: {out['meta']['centroid_source']})")
    print(f"port: Raigad {pk[4]} km (norm {norm_port[4]:.3f}) | Ratnagiri {pk[5]} km (norm {norm_port[5]:.3f})")
