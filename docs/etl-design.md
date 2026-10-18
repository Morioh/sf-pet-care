# ETL Design

**Author:** Ahmad Naggayev · **Branch:** `ahmad` · **Updated:** 2026-10-16

The ETL combines the team's three data sources into one table: pet-care businesses per
San Francisco neighborhood, by type. All ETL code lives in `api/main.py`. The FastAPI
endpoints that serve the data live in the same file.

---

## 1. Flow

```
api/collectors/neighborhood.py          (Jonah)   ─┐
api/collectors/registered_businesses.py (Maurice) ─┼─>  api/main.py  ─>  GCS bucket
api/collectors/acs_data.py              (Jessie)  ─┘    run_etl()        processed/pet_care_by_neighborhood.csv
```

`POST /etl` runs `run_etl()`:

1. **Extract:** refresh all three source files in the bucket (`upload_neighborhood`,
   `upload_registered_businesses`, `upload_acs`), then read them back.
2. **Check:** `check_sources()` stops the run if a source is empty or a business
   neighborhood has no matching boundary.
3. **Transform:** `combine()` counts businesses per neighborhood and type, keeps all 41
   neighborhoods (zeros included) and adds a `businesses_total` column.
4. **Load:** `save_result()` writes the CSV to `processed/` in the bucket.

`python main.py` runs the same ETL from the command line.

---

## 2. Sources

Each collector is plain Python with `upload_*(dev_mode)` and `download_*(dev_mode)`.
`dev_mode=True` uses the `-dev` bucket and `False` uses `-prod`, per the work contract.

| Owner | Source | Bucket file | Rows |
|---|---|---|---|
| Jonah | SF Analysis Neighborhoods (GeoJSON) | `neighborhood.geojson` | 41 polygons |
| Maurice | SF Registered Business Locations (API) | `registered_businesses.json` | 374 pet-care businesses |
| Jessie | Census ACS 5-year, SF tracts (API) | `acs.json` | 244 tracts |

The Census API needs a key: without one it redirects to a "missing key" page. So `POST /etl`
needs `CENSUS_API_KEY` set in `api/.env` locally and in the Cloud Run environment. If it's
missing, the run stops with `CENSUS_API_KEY is not set`. `GET /census` only reads the bucket
and works without a key.

---

## 3. Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/neighborhoods` | neighborhood boundaries (GeoJSON) |
| GET | `/businesses` | pet-care businesses (raw) |
| GET | `/census` | census population and income per tract (raw) |
| GET | `/processed` | business counts per neighborhood (ETL output) |
| POST | `/etl` | runs the ETL, returns row counts (neighborhoods, businesses, census tracts) and the saved file path |

GET only reads. POST is the only endpoint that changes data in the bucket, so it's the one
the Cloud Scheduler job is meant to call. One scheduled POST refreshes all three sources.

---

## 4. Decisions

**Pet businesses are classified by NAICS code, not by name.** Searching names for "pet"
matches carpet companies and Petaluma addresses. Maurice's collector uses NAICS prefixes
(counts as of 2026-10-16; they change as the live data changes):

| Prefix | Category | Count |
|---|---|---|
| `54194` | veterinary | 63 |
| `81291` | pet_care (grooming, boarding, walking) | 302 |
| `45391` | pet_store | 9 |

**There's no spatial join.** The business data already names each business's neighborhood,
and all 35 names that appear match Jonah's boundary names exactly, so the join is a plain
merge on `neighborhood`. 366 of 374 businesses are counted. The other 8 have no neighborhood
and no coordinates, so a spatial join wouldn't place them either.

---

## 5. Limitation

Census data isn't joined to neighborhoods. It comes as 244 tracts keyed by `geoid`, while
businesses are grouped into 41 named neighborhoods, and the two share no key. So per-capita
rates aren't calculated. Census is still stored and served raw at `/census`. The fix is SF's
tract-to-neighborhood lookup table, which would make it one merge and one sum.

---

## 6. Credentials

No keys in code or images. Values come from `api/.env` (template: `api/.env_template`).
`.gitignore`, `.dockerignore` and `.gcloudignore` keep `.env` out of git and out of the
Docker images.
