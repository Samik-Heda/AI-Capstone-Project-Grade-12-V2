# Data changelog

Any correction to `districts_raw.csv`, `ports.csv` or `fields.json` must be cited here, with the field
status changed to Sourced where appropriate (spec §4.7).

## 2026-10-01: initial v2 dataset
- `districts_raw.csv` and `ports.csv` copied verbatim from the spec (§4.2, §4.4).
- District polygons: datta07/INDIAN-SHAPEFILES `STATES/MAHARASHTRA/MAHARASHTRA_DISTRICTS.geojson`
  (2011 census boundaries, 36 districts incl. Palghar). Name aliases: Ahmadnagar, Bid, Buldana, Gondiya,
  Raigarh, Mumbai → master names.
- Port distance is derived from the area-weighted polygon centroid (`data/centroids.csv`).
