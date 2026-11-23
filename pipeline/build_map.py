"""Build frontend/public/map.json and data/centroids.csv from district GeoJSON (spec §13).

Usage: python pipeline/build_map.py [--source PATH_OR_URL]

The generated map.json is committed so normal builds need no internet. The raw
GeoJSON is cached under data/raw/ (git-ignored).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import urllib.request
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import polylabel, transform

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend import config  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
SOURCES = [
    # State-only file from the same repo (13 MB) is tried first; the India-wide file is the spec's source.
    "https://raw.githubusercontent.com/datta07/INDIAN-SHAPEFILES/master/STATES/MAHARASHTRA/MAHARASHTRA_DISTRICTS.geojson",
    "https://raw.githubusercontent.com/datta07/INDIAN-SHAPEFILES/master/INDIA/INDIA_DISTRICTS.geojson",
]
STATE_KEYS = ["ST_NM", "STATE", "state_name", "stname"]
DISTRICT_KEYS = ["DISTRICT", "NAME_2", "district", "dtname"]
ALIASES = {
    "AHMADNAGAR": "Ahmednagar", "AHILYANAGAR": "Ahmednagar",
    "CHHATRAPATI SAMBHAJINAGAR": "Aurangabad", "BID": "Beed", "BULDANA": "Buldhana",
    "GONDIYA": "Gondia", "DHARASHIV": "Osmanabad", "RAIGARH": "Raigad", "MUMBAI": "Mumbai City",
}

VIEW_W, VIEW_H, PAD = 900, 750, 30
LAT_CORR = math.cos(math.radians(19.5))
SIMPLIFY_PX = 0.6


def load_geojson(source: str | None) -> dict:
    candidates = [source] if source else SOURCES
    for src in candidates:
        if not src.startswith("http"):
            return json.loads(Path(src).read_text(encoding="utf-8"))
        cached = RAW_DIR / src.rsplit("/", 1)[-1]
        if not cached.exists():
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            try:
                print(f"downloading {src}")
                urllib.request.urlretrieve(src, cached)
            except OSError as e:
                print(f"  failed: {e}")
                continue
        return json.loads(cached.read_text(encoding="utf-8"))
    raise SystemExit("no GeoJSON source available")


def pick_key(props: dict, keys: list[str]) -> str:
    for k in keys:
        if k in props:
            return k
    raise SystemExit(f"none of {keys} in feature properties {list(props)}")


def name_index(districts: list[dict]) -> dict[str, int]:
    idx = {}
    for d in districts:
        for n in [d["name"], *d["alt_names"]]:
            idx.setdefault(n.upper(), d["id"])
    for alias, canonical in ALIASES.items():
        idx[alias] = next(d["id"] for d in districts if d["name"] == canonical)
    # "Mumbai" must resolve to the City district, never Suburban.
    idx["MUMBAI"] = 1
    return idx


def ring_path(coords) -> str:
    pts = list(coords)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    head = f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"
    return head + "".join(f"L{x:.1f} {y:.1f}" for x, y in pts[1:]) + "Z"


def geom_path(geom) -> str:
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    parts = []
    for p in polys:
        if p.is_empty:
            continue
        parts.append(ring_path(p.exterior.coords))
        parts.extend(ring_path(r.coords) for r in p.interiors)
    return "".join(parts)


def simplify_keep_parts(geom, tol: float):
    """Douglas–Peucker per part so small islands/enclaves are never dropped."""
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    out = []
    for p in polys:
        s = p.simplify(tol, preserve_topology=True)
        if s.is_empty or s.area == 0:
            s = p  # too small to simplify safely: keep the original part
        out.extend(s.geoms if isinstance(s, MultiPolygon) else [s])
    return MultiPolygon(out) if len(out) > 1 else out[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="local GeoJSON path or URL")
    args = ap.parse_args()

    districts = json.loads((ROOT / "data" / "districts.json").read_text(encoding="utf-8"))
    by_name = name_index(districts)
    gj = load_geojson(args.source)

    feats = gj["features"]
    skey = pick_key(feats[0]["properties"], STATE_KEYS)
    dkey = pick_key(feats[0]["properties"], DISTRICT_KEYS)
    feats = [f for f in feats if "maharashtra" in str(f["properties"][skey]).lower()]

    geoms: dict[int, object] = {}
    unmatched = []
    for f in feats:
        raw_name = str(f["properties"][dkey]).strip().upper()
        did = by_name.get(raw_name)
        if did is None:
            unmatched.append(raw_name)
            continue
        g = shape(f["geometry"])
        if not g.is_valid:
            g = g.buffer(0)
        geoms[did] = g.union(geoms[did]) if did in geoms else g
    if unmatched:
        raise SystemExit(f"unmatched district names: {unmatched}")
    if sorted(geoms) != list(range(1, 37)):
        missing = sorted(set(range(1, 37)) - set(geoms))
        raise SystemExit(f"expected 36 matched ids, got {len(geoms)}; missing {missing}")

    # Projection: equirectangular with latitude correction, then uniform fit into the viewBox.
    minx = min(g.bounds[0] for g in geoms.values())
    miny = min(g.bounds[1] for g in geoms.values())
    maxx = max(g.bounds[2] for g in geoms.values())
    maxy = max(g.bounds[3] for g in geoms.values())
    lon0, lat0 = minx, maxy
    w = (maxx - minx) * LAT_CORR
    h = maxy - miny
    scale = min((VIEW_W - 2 * PAD) / w, (VIEW_H - 2 * PAD) / h)
    off_x = (VIEW_W - w * scale) / 2
    off_y = (VIEW_H - h * scale) / 2

    def project(lon, lat, z=None):
        return ((lon - lon0) * LAT_CORR * scale + off_x, -(lat - lat0) * scale + off_y)

    def unproject(x, y):
        return ((x - off_x) / (LAT_CORR * scale) + lon0, -(y - off_y) / scale + lat0)

    out_districts = []
    centroids = []
    for d in districts:
        g_proj = transform(project, geoms[d["id"]])
        # Area-weighted centroid in the (locally equal-area) projected plane, mapped back to lat/lon.
        c = g_proj.centroid
        clon, clat = unproject(c.x, c.y)
        centroids.append((d["id"], round(clat, 5), round(clon, 5)))

        simp = simplify_keep_parts(g_proj, SIMPLIFY_PX)
        largest = max(simp.geoms, key=lambda p: p.area) if isinstance(simp, MultiPolygon) else simp
        lab = polylabel(largest, tolerance=0.5)
        out_districts.append({
            "id": d["id"], "name": d["name"], "d": geom_path(simp),
            "label_x": round(lab.x, 1), "label_y": round(lab.y, 1),
            "parts": len(simp.geoms) if isinstance(simp, MultiPolygon) else 1,
        })

    ids = [x["id"] for x in out_districts]
    assert len(ids) == len(set(ids)) == 36, "duplicate or missing district ids"


    map_json = {
        "viewBox": f"0 0 {VIEW_W} {VIEW_H}",
        "districts": out_districts,
    }
    out_map = config.path("map_json")
    out_map.parent.mkdir(parents=True, exist_ok=True)
    out_map.write_text(json.dumps(map_json, separators=(",", ":")), encoding="utf-8")

    with open(config.path("centroids_csv"), "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["id", "lat", "lon"])
        wr.writerows(centroids)

    print(f"map.json: 36 districts, {out_map.stat().st_size / 1024:.0f} KB")
    print(f"centroids.csv: {len(centroids)} rows")


if __name__ == "__main__":
    main()
