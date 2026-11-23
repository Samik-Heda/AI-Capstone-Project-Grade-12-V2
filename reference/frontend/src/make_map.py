"""
make_map.py — Downloads Maharashtra district GeoJSON and converts to SVG paths.
Run from the frontend/src directory. Writes maharashtraMap.js.

Tries multiple GeoJSON sources in order until one works.
Requires: requests (pip install requests)
"""

import json, math, sys, os, urllib.request, urllib.error

SOURCES = [
    "https://raw.githubusercontent.com/datta07/INDIAN-SHAPEFILES/master/INDIA/INDIA_DISTRICTS.geojson",
]

# Known district name normalizations (GeoJSON names → our DB names)
# Keys are UPPERCASE as they appear in the source dataset
NAME_MAP = {
    "AHMEDNAGAR": "Ahmednagar", "AHMADNAGAR": "Ahmednagar",
    "AURANGABAD": "Aurangabad", "CHHATRAPATI SAMBHAJINAGAR": "Aurangabad",
    "BEED": "Beed", "BID": "Beed",
    "BHANDARA": "Bhandara",
    "BULDHANA": "Buldhana", "BULDANA": "Buldhana",
    "CHANDRAPUR": "Chandrapur",
    "DHULE": "Dhule",
    "GADCHIROLI": "Gadchiroli",
    "GONDIA": "Gondia", "GONDIYA": "Gondia",
    "HINGOLI": "Hingoli",
    "JALGAON": "Jalgaon",
    "JALNA": "Jalna",
    "KOLHAPUR": "Kolhapur",
    "LATUR": "Latur",
    "MUMBAI": "Mumbai City",
    "MUMBAI SUBURBAN": "Mumbai Suburban",
    "NAGPUR": "Nagpur",
    "NANDED": "Nanded",
    "NANDURBAR": "Nandurbar",
    "NASHIK": "Nashik",
    "OSMANABAD": "Osmanabad", "DHARASHIV": "Osmanabad",
    "PALGHAR": "Palghar",
    "PARBHANI": "Parbhani",
    "PUNE": "Pune",
    "RAIGAD": "Raigad", "RAIGARH": "Raigad",
    "RATNAGIRI": "Ratnagiri",
    "SANGLI": "Sangli",
    "SATARA": "Satara",
    "SINDHUDURG": "Sindhudurg",
    "SOLAPUR": "Solapur",
    "THANE": "Thane",
    "WARDHA": "Wardha",
    "WASHIM": "Washim",
    "YAVATMAL": "Yavatmal",
    "AKOLA": "Akola",
    "AMRAVATI": "Amravati",
    "AKOLA": "Akola",
    "WASHIM": "Washim",
}

DB_IDS = {
    "Mumbai City": 1, "Mumbai Suburban": 2, "Thane": 3, "Raigad": 4,
    "Ratnagiri": 5, "Sindhudurg": 6, "Palghar": 7, "Nashik": 8,
    "Ahmednagar": 9, "Dhule": 10, "Nandurbar": 11, "Jalgaon": 12,
    "Pune": 13, "Satara": 14, "Sangli": 15, "Solapur": 16,
    "Kolhapur": 17, "Aurangabad": 18, "Jalna": 19, "Beed": 20,
    "Latur": 21, "Osmanabad": 22, "Nanded": 23, "Hingoli": 24,
    "Parbhani": 25, "Nagpur": 26, "Wardha": 27, "Yavatmal": 28,
    "Amravati": 29, "Akola": 30, "Washim": 31, "Buldhana": 32,
    "Chandrapur": 33, "Gadchiroli": 34, "Gondia": 35, "Bhandara": 36,
}


def fetch_geojson(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"  Failed: {e}")
        return None


def haversine_centroid(coords):
    """Approximate centroid of a polygon ring."""
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def geo_to_svg(features, vw=900, vh=750, padding=30):
    """Project lon/lat features to SVG coordinates."""
    # Collect all points to find bounds
    all_pts = []
    for f in features:
        geom = f["geometry"]
        rings = geom["coordinates"]
        if geom["type"] == "Polygon":
            rings = [rings[0]]
        elif geom["type"] == "MultiPolygon":
            rings = [p[0] for p in rings]
        for ring in rings:
            all_pts.extend(ring)

    min_lon = min(p[0] for p in all_pts)
    max_lon = max(p[0] for p in all_pts)
    min_lat = min(p[1] for p in all_pts)
    max_lat = max(p[1] for p in all_pts)

    usable_w = vw - 2 * padding
    usable_h = vh - 2 * padding
    scale = min(usable_w / (max_lon - min_lon), usable_h / (max_lat - min_lat))

    def project(lon, lat):
        x = padding + (lon - min_lon) * scale
        # Flip Y (SVG y=0 is top, latitude increases upward)
        y = vh - padding - (lat - min_lat) * scale
        return round(x, 1), round(y, 1)

    results = []
    for f in features:
        props = f["properties"]
        raw_name = props.get("NAME_2") or props.get("district") or props.get("DISTRICT") or props.get("name") or ""
        name = NAME_MAP.get(raw_name, raw_name)
        db_id = DB_IDS.get(name)
        if not db_id:
            print(f"  Warning: no DB id for '{raw_name}' → '{name}'")
            continue

        geom = f["geometry"]
        if geom["type"] == "Polygon":
            polys = [geom["coordinates"]]
        else:
            polys = geom["coordinates"]

        # Pick largest polygon ring
        best_ring = max((p[0] for p in polys), key=len)

        # Simplify: keep every Nth point so paths aren't massive
        step = max(1, len(best_ring) // 120)
        ring = best_ring[::step]

        path_parts = []
        for i, pt in enumerate(ring):
            x, y = project(pt[0], pt[1])
            path_parts.append(f"{'M' if i == 0 else 'L'}{x},{y}")
        path_parts.append("Z")
        d = " ".join(path_parts)

        # Centroid for label
        cx, cy = haversine_centroid(best_ring)
        cx, cy = project(cx, cy)

        results.append({"id": db_id, "name": name, "cx": cx, "cy": cy, "d": d})

    results.sort(key=lambda r: r["id"])
    return results


def write_js(districts, out_path):
    lines = [
        "// Maharashtra district SVG paths — generated from real GeoJSON boundaries",
        "// Do not edit manually. Re-run make_map.py to regenerate.",
        "",
        'export const VIEWBOX = "0 0 900 750";',
        "",
        "export const DISTRICT_PATHS = [",
    ]
    for d in districts:
        lines.append(f'  {{ id: {d["id"]}, name: "{d["name"]}", cx: {d["cx"]}, cy: {d["cy"]}, d: "{d["d"]}" }},')
    lines += [
        "];",
        "",
        "export function getDistrictColor(rank) {",
        "  if (!rank) return '#1B2537';",
        "  if (rank === 1) return '#38BDF8';",
        "  if (rank === 2) return '#2DD4BF';",
        "  if (rank === 3) return '#22D3EE';",
        "  if (rank <= 5) return '#1D9FBF';",
        "  if (rank <= 10) return '#1A6B8A';",
        "  return '#1B2537';",
        "}",
    ]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {len(districts)} districts to {out_path}")


def filter_maharashtra(features):
    """Keep only Maharashtra features, auto-detecting property keys."""
    if not features:
        return features
    sample = features[0]["properties"]
    print(f"  Property keys: {list(sample.keys())}")

    # Detect state key
    state_key = None
    for k in ["ST_NM", "STATE", "state_name", "STATENAME", "state", "State"]:
        if k in sample:
            state_key = k
            break

    # Detect district key
    dist_key = None
    for k in ["DISTRICT", "NAME_2", "district", "District", "name", "NAME"]:
        if k in sample:
            dist_key = k
            break

    print(f"  State key: {state_key}, District key: {dist_key}")

    if state_key:
        maha = [f for f in features if "maharashtra" in str(f["properties"].get(state_key, "")).lower()]
        print(f"  Maharashtra features: {len(maha)}")
        return maha, dist_key
    return features, dist_key


def geo_to_svg_with_key(features, dist_key, vw=900, vh=750, padding=40):
    """Project lon/lat features to SVG coordinates using detected district key."""
    all_pts = []
    for f in features:
        geom = f["geometry"]
        if geom["type"] == "Polygon":
            rings = [geom["coordinates"][0]]
        else:
            rings = [p[0] for p in geom["coordinates"]]
        for ring in rings:
            all_pts.extend(ring)

    min_lon = min(p[0] for p in all_pts)
    max_lon = max(p[0] for p in all_pts)
    min_lat = min(p[1] for p in all_pts)
    max_lat = max(p[1] for p in all_pts)

    usable_w = vw - 2 * padding
    usable_h = vh - 2 * padding
    scale = min(usable_w / (max_lon - min_lon), usable_h / (max_lat - min_lat))
    # Center it
    map_w = (max_lon - min_lon) * scale
    map_h = (max_lat - min_lat) * scale
    off_x = padding + (usable_w - map_w) / 2
    off_y = padding + (usable_h - map_h) / 2

    def project(lon, lat):
        x = off_x + (lon - min_lon) * scale
        y = vh - off_y - (lat - min_lat) * scale
        return round(x, 1), round(y, 1)

    results = []
    for f in features:
        props = f["properties"]
        raw_name = str(props.get(dist_key, "")).strip().upper() if dist_key else ""
        name = NAME_MAP.get(raw_name, raw_name.title())
        db_id = DB_IDS.get(name)
        if not db_id:
            print(f"  Skipping '{raw_name}' -> '{name}' (no DB id)")
            continue

        geom = f["geometry"]
        if geom["type"] == "Polygon":
            polys = [geom["coordinates"]]
        else:
            polys = geom["coordinates"]

        best_ring = max((p[0] for p in polys), key=len)
        step = max(1, len(best_ring) // 150)
        ring = best_ring[::step]

        parts = [f"M{project(ring[0][0], ring[0][1])[0]},{project(ring[0][0], ring[0][1])[1]}"]
        for pt in ring[1:]:
            x, y = project(pt[0], pt[1])
            parts.append(f"L{x},{y}")
        parts.append("Z")

        cx_lon, cy_lat = haversine_centroid(best_ring)
        cx, cy = project(cx_lon, cy_lat)

        results.append({"id": db_id, "name": name, "cx": cx, "cy": cy, "d": " ".join(parts)})

    results.sort(key=lambda r: r["id"])
    return results


def main():
    out = os.path.join(os.path.dirname(__file__), "maharashtraMap.js")
    geojson = None
    for url in SOURCES:
        print(f"Trying: {url}")
        geojson = fetch_geojson(url)
        if geojson and geojson.get("features"):
            print(f"  Got {len(geojson['features'])} total features")
            break

    if not geojson:
        print("All sources failed.")
        sys.exit(1)

    maha_features, dist_key = filter_maharashtra(geojson["features"])
    if len(maha_features) < 30:
        print(f"Only {len(maha_features)} Maharashtra features found. Printing all state values:")
        states = set(str(f["properties"].get("ST_NM", f["properties"].get("STATE", "?"))) for f in geojson["features"][:50])
        print(states)
        sys.exit(1)

    districts = geo_to_svg_with_key(maha_features, dist_key)
    print(f"Matched {len(districts)} districts to DB")
    if len(districts) < 30:
        sys.exit(1)

    write_js(districts, out)


if __name__ == "__main__":
    main()
