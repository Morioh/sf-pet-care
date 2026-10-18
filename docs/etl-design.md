# ETL Design

**Author:** Ahmad Naggayev · **Branch:** `ahmad` · **Updated:** 2026-10-16

The ETL combines the team's three data sources into one table: pet-care businesses per
San Francisco neighborhood, by type, with each neighborhood's population and businesses per
1,000 residents. All ETL code lives in `api/main.py`. The FastAPI
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
2. **Check:** `check_sources()` stops the run if a source is empty, a business
   neighborhood has no matching boundary, or the census data has no coordinates.
3. **Transform:** `combine()` counts businesses per neighborhood and type, keeps all 41
   neighborhoods (zeros included) and adds a `businesses_total` column. It then adds each
   neighborhood's `population` (summed from census tracts, see section 5) and
   `businesses_per_1000`, which is left empty for a neighborhood with no residents.
4. **Load:** `save_result()` writes the CSV to `processed/` in the bucket.

`python main.py` runs the same ETL from the command line.

---

## 2. Sources

Each collector is plain Python with `upload_*(dev_mode)` and `download_*(dev_mode)`.
`dev_mode=True` uses the `-dev` bucket and `False` uses `-prod`, per the work contract.
Endpoints default to `-dev` when run locally. The Cloud Run deploy sets `DEV_MODE=false`,
so the deployed API and its nightly ETL use `-prod`.

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
| GET | `/processed` | business counts, population and businesses per 1,000 residents per neighborhood (ETL output) |
| POST | `/etl` | runs the ETL, returns row counts (neighborhoods, businesses, census tracts, residents) and the saved file path |

GET only reads. POST is the only endpoint that changes data in the bucket, so it's the one
the Cloud Scheduler job is meant to call. One scheduled POST refreshes all three sources.

On Cloud Run, `/etl` only accepts the scheduler: the job sends a Google-signed ID token for
the `pet-etl-scheduler` service account, and `check_etl_caller()` rejects any other caller
(`ETL_INVOKER` and `ETL_AUDIENCE` are set by the deploy). Run locally, those are unset, so
`/etl` stays open for development. The GET endpoints stay public for the dashboard.

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

**Businesses don't need a spatial join.** The business data already names each business's
neighborhood, and all 35 names that appear match Jonah's boundary names exactly, so the join
is a plain merge on `neighborhood`. 366 of 374 businesses are counted. The other 8 have no neighborhood
and no coordinates, so a spatial join wouldn't place them either.

---

## 5. Census tracts to neighborhoods

Census data comes as 244 tracts keyed by `geoid`, and tracts share no key with the 41
neighborhoods. `population_by_neighborhood()` places each tract at its center point (the
`lat`/`lon` from the Census gazetteer) and assigns it to the nearest neighborhood polygon,
which for a point inside a polygon is the polygon itself. Nearest rather than "inside" is
needed because a few shoreline tracts' center points fall just outside every polygon
(tract 231.03 has 4,545 residents). Every resident is counted: neighborhood populations sum
to the census total (830,235 as of 2026-10-17).

Limitations:

- A tract that straddles two neighborhoods counts entirely toward the one containing its
  center. SF's official tract-to-neighborhood lookup table would split those more precisely.
- Small populations make the rate swing. Golden Gate Park has 58 residents and 1 business,
  so it shows 17.24 per 1,000. Read `businesses_per_1000` alongside `population`.
- Median income is per tract and can't be summed, so it isn't rolled up to neighborhoods.
  It's still served per tract at `/census`.

---

## 6. Credentials

No keys in code or images. Values come from `api/.env` (template: `api/.env_template`).
`.gitignore`, `.dockerignore` and `.gcloudignore` keep `.env` out of git and out of the
Docker images.
