# Unified ETL — Design

**Author:** Ahmad Naggayev · **Branch:** `ahmad` · **Updated:** 2026-10-04

I own the integration layer: the code that takes what each source owner produces and turns
it into one clean, joined dataset. I don't control how anyone fetches their data — only what
each source hands over.

**Final metric (needs team sign-off):** pet-care businesses per 1,000 residents, per neighborhood.

---

## 1. Shape

```
Maurice  — SF business registry (API)        ─┐
Jessie   — Census ACS population (API)       ─┼──>  etl/client.py  ──>  GCS bucket
Jonah    — SF neighborhood boundaries (file) ─┘         │
                                                        ├─ validate (etl/contract.py)
                                                        ├─ join on neighborhood name
                                                        ├─ join population
                                                        └─ compute metric
```

---

## 2. Source interfaces

All three collectors are plain Python — no FastAPI in the pipeline. Each exposes
`upload_*(dev_mode)` and `download_*(dev_mode)`, and `dev_mode` picks the `-dev` or `-prod`
bucket per the work contract.

| Owner | Download function | Returns | File written |
|---|---|---|---|
| Maurice | `download_registered_businesses(dev_mode)` | DataFrame, 374 rows | `registered_businesses.json` |
| Jonah | `download_neighborhood(dev_mode)` | GeoDataFrame, 41 polygons | `neighborhood.geojson` |
| Jessie | ⏳ not yet — `clean_acs_data()` returns a list of dicts | 244 tracts | `acs_sf_tracts_<DATE>.json` |

**Column specs live in `etl/contract.py`, not in this document.** One source of truth; copying
them here would only let the two drift apart.

Maurice and Jonah now share the same `get_client(dev_mode)` pattern, the same three
environment variables, and both overwrite a single file. Jessie's script still uses
`GCP_DEV_BUCKET` and writes a new dated file each run — worth aligning.

### Open ask for Jessie

```python
download_acs(dev_mode=True) -> pd.DataFrame   # geoid as str, population as int
```

Her `clean_acs_data()` already produces the right records; it needs wrapping in a
DataFrame and a bucket read, matching the pattern the other two use.

---

## 3. Verified findings

Checked against the live sources on 2026-10-03/04. Several README assumptions did not hold.

**Pet businesses cannot be found by name.** Searching `dba_name` for "pet" returns 1,914 rows
including *Abbey Carpet*, *24/7 Carpet Care* and addresses in *Petaluma*. Classification uses
NAICS prefixes instead, matched by prefix because the codes are self-reported and sometimes
truncated:

| Prefix | Category | Count |
|---|---|---|
| `54194` | Veterinary services | 61 |
| `81291` | Pet care — grooming, boarding, walking | 302 |
| `45391` | Pet and pet supplies stores | 11 |

**The raw feed needs filtering.** 367,825 records total → 295,739 in San Francisco → 102,119
still open → 99,399 with coordinates. It includes closed businesses and businesses registered
in SF but located elsewhere.

**`location` arrives as a nested GeoJSON Point** with coordinates ordered **[longitude, latitude]**.
Maurice's script flattens it to `longitude` / `latitude` columns and drops the original.

**The Census API returns HTTP 200 on failure** — without a key it serves an HTML page titled
"Missing Key", not a 401. Code that only checks the status code will parse garbage.

**Both API URLs redirect** (`data.sfgov.org` → `data.sf.gov`). `httpx` and `requests` need
`follow_redirects` / `-L`.

**The GeoJSON is one FeatureCollection**, 41 MultiPolygon features, name in property `nhood`
(Jonah renames it to `neighborhood`). Must be split to newline-delimited before any BigQuery load.

---

## 4. Pipeline

```python
# etl/client.py
businesses    = load_businesses(dev_mode)     # Maurice's download_registered_businesses()
neighborhoods = load_neighborhoods(dev_mode)  # Jonah's download_neighborhood()
census        = load_acs(dev_mode)            # None until Jessie ships hers

check_businesses(businesses)                  # etl/contract.py
check_neighborhoods(neighborhoods)
check_join(businesses, neighborhoods)         # assert the join before relying on it

result = integrate_sources(businesses, neighborhoods, census)
save_output(result, dev_mode)                 # processed/ in the bucket
```

Each source sits behind a small adapter so a missing interface is visible rather than fatal.
When Jessie's function lands, `load_acs()` becomes one line.

---

## 5. Decisions

### Spatial join is not needed — RESOLVED 2026-10-04

Measured on live data: **all 35 neighbourhood values in the business file match a boundary
name exactly. Zero orphans.** The column is 99.9% populated (99,288 of 99,399 rows). Six
boundaries simply have no pet businesses.

Maurice now emits the column as `neighborhood`, the same name Jonah uses, so the join is a
plain merge. No `ST_CONTAINS`, no GeoPandas in the pipeline, no CRS alignment. `check_join()`
asserts it rather than trusting it.

The 8 businesses with no neighbourhood are the **same 8** that have no coordinates, so a
spatial join would recover none of them.

### ⚠️ OPEN — census tracts → neighbourhoods

| | |
|---|---|
| Census gives | 244 tracts keyed by 11-char `geoid` |
| Boundaries give | 41 neighbourhoods keyed by name |
| Shared key | **none** |

Without a mapping there is no population denominator and the headline metric cannot be
computed. This is the last real blocker.

Options: a published SF tract↔neighbourhood crosswalk, or compute tract centroids and test
which polygon contains each — the one place GeoPandas is still justified, and only for 244
rows once, not per business per run.

Whichever we choose goes in the README, because it changes the numbers.

### Still to confirm

- Raw and processed stored separately, or one output for now?
- Confirm the metric is pet businesses per 1,000 residents.

---

## 6. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Tract→neighbourhood unmapped | no denominator, no metric | section 5 — needs a team decision |
| No service-account key locally | `client.py` writes output to disk instead of GCS | accept the GCP owner invite, then mint a key |
| Pet classification rule | determines the entire result | NAICS prefixes, documented in README |
| Jessie's file naming differs | ETL must guess which file is current | align on overwrite-single-file, as Maurice and Jonah now do |
| 8 of 374 businesses unplaced | small undercount | `check_businesses` reports the count every run |
| `geoid` read as int | leading zero lost, join fails silently | `check_census` asserts 11 characters |
| Census key missing | HTML returned with HTTP 200 | validate the body, key in `.env` |

---

## 7. Credentials

No keys in code. Census key in `.env` as `CENSUS_API_KEY`, listed in `.env_template`, loaded
with `python-dotenv`. GCP access via `GCP_SERVICE_ACCOUNT_KEY` pointing at a file path that is
never committed — `.gitignore` covers `.env`, `*-key.json` and `service-account*.json`.
