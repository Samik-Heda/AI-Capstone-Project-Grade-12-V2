"""SQLite access for data/gc.db plus the static registry files in data/."""
from __future__ import annotations

import csv
import json
import sqlite3
from functools import lru_cache

from . import config


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.path("db"))
    con.row_factory = sqlite3.Row
    return con


@lru_cache(maxsize=1)
def registry() -> dict:
    return json.loads((config.DATA_DIR / "fields.json").read_text(encoding="utf-8"))


def fields() -> list[dict]:
    return registry()["fields"]


def field_keys() -> list[str]:
    return [f["key"] for f in fields()]


@lru_cache(maxsize=1)
def industry_needs_all() -> dict[str, list[dict]]:
    """§19.3 curated 'also check' needs per preset (general guidance, not district data)."""
    return json.loads((config.DATA_DIR / "industry_needs.json").read_text(encoding="utf-8"))["needs"]


def industry_needs(profile_id: str | None) -> list[dict]:
    """Needs for a preset, or for the preset a user profile was built from; [] when unknown."""
    if not profile_id:
        return []
    allp = industry_needs_all()
    if profile_id in allp:
        return allp[profile_id]
    with connect() as con:  # a user profile: the preset it was built from
        r = con.execute("SELECT base_profile_id FROM profile WHERE id = ?", (profile_id,)).fetchone()
    return allp.get((r["base_profile_id"] if r else None) or "", [])


@lru_cache(maxsize=1)
def ports() -> list[dict]:
    with open(config.DATA_DIR / "ports.csv", newline="", encoding="utf-8") as f:
        return [{"name": p["name"], "lat": float(p["lat"]), "lon": float(p["lon"])} for p in csv.DictReader(f)]


def districts() -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT * FROM district ORDER BY id").fetchall()
    return [
        {"id": r["id"], "name": r["name"], "region": r["region"], "metro_core": r["metro_core"],
         "alt_names": json.loads(r["alt_names"]), "population": r["population"],
         "centroid": [r["centroid_lat"], r["centroid_lon"]]}
        for r in rows
    ]


def profiles() -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT id, kind, name, rationale FROM profile ORDER BY kind, rowid").fetchall()
    return [dict(r) for r in rows]


def meta_value(key: str) -> str | None:
    with connect() as con:
        row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
