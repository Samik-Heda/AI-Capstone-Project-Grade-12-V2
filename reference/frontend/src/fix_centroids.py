"""
fix_centroids.py — Recomputes cx/cy for each district in maharashtraMap.js
using the proper polygon centroid formula (shoelace theorem) instead of
the mean-of-vertices approach that biases toward dense edge regions.

Run from frontend/src:  python fix_centroids.py
"""

import re, os

MAP_JS = os.path.join(os.path.dirname(__file__), "maharashtraMap.js")


def parse_path(d):
    """Extract polygon vertices from SVG path string (M x,y L x,y ... Z)."""
    coords = []
    for m in re.finditer(r"[ML]([\d.]+),([\d.]+)", d):
        coords.append((float(m.group(1)), float(m.group(2))))
    return coords


def polygon_centroid(coords):
    """Proper area-weighted centroid via shoelace formula."""
    n = len(coords)
    if n < 3:
        return sum(c[0] for c in coords) / n, sum(c[1] for c in coords) / n
    area = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x0, y0 = coords[i]
        x1, y1 = coords[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    area /= 2.0
    if abs(area) < 1e-6:
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return sum(xs) / n, sum(ys) / n
    cx /= (6.0 * area)
    cy /= (6.0 * area)
    return cx, cy


with open(MAP_JS, "r", encoding="utf-8") as f:
    content = f.read()

pattern = re.compile(
    r'(\{ id: \d+, name: "[^"]+", cx: )([\d.]+)(, cy: )([\d.]+)(, d: ")(.*?)(" \})',
    re.DOTALL
)

updated = 0

def replacer(m):
    global updated
    prefix_id_cx = m.group(1)
    old_cx = m.group(2)
    mid_cy = m.group(3)
    old_cy = m.group(4)
    prefix_d = m.group(5)
    d = m.group(6)
    suffix = m.group(7)

    coords = parse_path(d)
    if len(coords) < 3:
        return m.group(0)

    new_cx, new_cy = polygon_centroid(coords)
    new_cx = round(new_cx, 1)
    new_cy = round(new_cy, 1)

    if new_cx != float(old_cx) or new_cy != float(old_cy):
        updated += 1
        print(f"  Updated centroid: cx {old_cx} -> {new_cx}, cy {old_cy} -> {new_cy}")

    return f"{prefix_id_cx}{new_cx}{mid_cy}{new_cy}{prefix_d}{d}{suffix}"


new_content = pattern.sub(replacer, content)

with open(MAP_JS, "w", encoding="utf-8") as f:
    f.write(new_content)

print(f"\nDone. {updated} centroids updated in maharashtraMap.js")
