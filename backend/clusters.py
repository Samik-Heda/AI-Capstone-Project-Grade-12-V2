"""District types (spec §10), visible ML: KMeans over the 13 normalized fields, z-scored and unweighted,
so clusters describe the structure of the districts, not the current ranking."""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

from . import templates
from .scoring import dataset

K_RANGE = range(3, 7)
PALETTE = ["#A78BFA", "#FBBF24", "#2DD4BF", "#FB7185", "#60A5FA", "#F472B6"]  # §12.6, 6 distinct
# A short type name from a cluster's strongest field; the full label lists the evidence.
THEME = {"crop_output": "Agri belt", "irrigation": "Irrigated farmland", "crop_diversity": "Mixed farming",
         "port": "Coastal", "estates": "Industrial hub", "msme": "Supplier base", "literacy": "Skilled workforce",
         "highway": "Highway corridor", "rail": "Rail corridor", "road_density": "Road network",
         "power": "Power-rich", "water": "Water-rich", "land": "Rural interior"}


def features() -> np.ndarray:
    """(36, 13) z-scores per field; constant fields stay 0."""
    x = dataset().norm
    sd = x.std(axis=0)
    return (x - x.mean(axis=0)) / np.where(sd > 0, sd, 1.0)


def _fit(z: np.ndarray, k: int) -> tuple[np.ndarray, float]:
    km = KMeans(n_clusters=k, n_init=20, random_state=7).fit(z)
    return km.labels_, float(silhouette_score(z, km.labels_))


def _labels(centroids: np.ndarray, keys: list[str]) -> list[dict]:
    """Top 2 positive centroid z-scores and the most negative one ("Agri belt: high farm produce,
    irrigated farmland; low small suppliers nearby"). Collisions get the next distinguishing field."""
    out = []
    for c in centroids:
        order = list(np.argsort(-c))
        pos = [keys[j] for j in order if c[j] > 0][:2] or [keys[order[0]]]
        neg = keys[int(np.argmin(c))]
        out.append({"pos": pos, "neg": neg, "order": [keys[j] for j in order], "theme": THEME[pos[0]]})
    # unique type names: a repeated theme moves on to the cluster's next strongest field
    for i, o in enumerate(out):
        used = {x["theme"] for x in out[:i]}
        for f in o["order"]:
            if THEME[f] not in used:
                o["theme"] = THEME[f]
                break

    def text(o: dict) -> str:
        hi = ", ".join(templates.plain_lc(f) for f in o["pos"])
        return f"{o['theme']}: high {hi}; low {templates.plain_lc(o['neg'])}"

    labels = [text(o) for o in out]
    for _ in range(len(keys)):
        dup = {lab for lab in labels if labels.count(lab) > 1}
        if not dup:
            break
        for i, o in enumerate(out):
            if labels[i] in dup:
                nxt = next((f for f in o["order"] if f not in o["pos"] and f != o["neg"]), None)
                if nxt:
                    o["pos"].append(nxt)
                labels[i] = text(o)
    return [{"label": lab, "name": o["theme"]} for lab, o in zip(labels, out)]


@lru_cache(maxsize=8)
def _compute(k: int | None, _ds_id: int) -> dict:
    ds = dataset()
    z = features()
    tried = {kk: _fit(z, kk) for kk in ([k] if k else K_RANGE)}
    best = max(tried, key=lambda kk: tried[kk][1])
    lab, sil = tried[best]
    # stable ids: biggest cluster first
    order = sorted(range(best), key=lambda c: (-int((lab == c).sum()), c))
    centroids = np.array([z[lab == c].mean(axis=0) for c in order])
    names = _labels(centroids, ds.keys)
    clusters = []
    for i, c in enumerate(order):
        clusters.append({"id": i, "label": names[i]["label"], "name": names[i]["name"], "color": PALETTE[i],
                         "district_ids": [ds.ids[r] for r in np.flatnonzero(lab == c)],
                         "centroid_z": {f: round(float(v), 3) for f, v in zip(ds.keys, centroids[i])}})
    return {"k": best, "silhouette": round(sil, 3),
            "silhouettes": {str(kk): round(v[1], 3) for kk, v in sorted(tried.items())}, "clusters": clusters}


def compute(k: int | None = None) -> dict:
    """§10 result, cached until the dataset changes (scoring.reload() makes a new Dataset)."""
    return _compute(k, id(dataset()))
