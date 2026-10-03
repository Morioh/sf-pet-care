# Unified ETL — Design Proposal

**Author:** Ahmad Naggayev · **Branch:** `ahmad` · **Status:** proposal for team review
**Date:** 2026-10-03

---

## 1. What this document is

I own the **unified ETL pipeline**. That is the integration layer: the code that takes
whatever each source owner produces and turns it into one clean, joined dataset.

I do **not** need to control how anyone fetches their data. I need us to agree on **what each
source returns** before we write code, so the pieces fit without rewrites later.

This document proposes that contract and flags what I verified against the live sources.

---

## 2. The shape of the thing

```
Maurice  — SF business registry (API)        ─┐
Jessie   — Census ACS population (API)       ─┼──>  UNIFIED ETL  ──>  GCS bucket
Jonah    — SF neighborhood boundaries (file) ─┘         │
                                                        ├─ standardize
                                                        ├─ classify pet businesses
                                                        ├─ assign neighborhood
                                                        ├─ join population
                                                        └─ compute metric
```

**Final metric (needs team sign-off):** pet-care businesses per 1,000 residents, per neighborhood.
Everything upstream exists to produce that number. If we change the metric, the transforms change.

---

## 3. Verified findings — things that differ from our README

I queried all three sources live on 2026-10-03. Several assumptions in our README do not hold.

### 3.1 The business dataset has 36 columns, not the 18 it returns by default

The API returns a subset unless you ask for more. The authoritative column list comes from
`https://data.sfgov.org/api/views/g8m3-pdis.json`.

**There is no `naic_code_description` field.** The real classification fields are:

| Field | Note |
|---|---|
| `self_reported_naics_code` | numeric NAICS, self-reported |
| `lic_code_description` | license category text |
| `lic_code_descriptions_list` | multiple categories |

Any contract naming `naic_code_description` will fail.

### 3.2 `location` is a nested GeoJSON Point, not lat/lon columns

```json
"location": {"type": "Point", "coordinates": [-122.0453, 37.96]}
```

Note the order: **longitude first, then latitude**. Flat `latitude` / `longitude` columns must be
extracted, they do not exist in the response.

### 3.3 The dataset already contains a neighborhood column

`neighborhoods_analysis_boundaries` — SF has already assigned each business to the same
41 "Analysis Neighborhoods" that Jonah's GeoJSON defines.

**This is the most important open question for the team.** If that column is populated well,
we may not need a spatial join at all. See section 7.

### 3.4 Row counts — the data needs filtering

| Filter | Rows |
|---|---|
| All records | 367,825 |
| `city = 'San Francisco'` | 295,739 |
| …and still open (`location_end_date IS NULL`) | 102,119 |
| …and has coordinates | **99,399** |

The raw feed includes closed businesses and businesses registered in SF but located elsewhere
(the first record I pulled was in Concord). ~3% of SF records have no coordinates.

### 3.5 Pet classification cannot be done by business name

Name matching on `dba_name`:

| Term | Matches | Problem |
|---|---|---|
| `pet` | 1,914 | matches **car**pet**, **Pet**aluma |
| `vet` | 366 | matches "Corvette", "Velvet" |
| `groom` | 82 | plausible |
| `paws` | 128 | plausible |
| `animal` | 103 | plausible |

Real samples returned for "pet": `Abbey Carpet`, `24/7 Carpet Care`, `5000 Puppets`.

**Classification must use NAICS / LIC codes.** This decision drives our entire result and
must be written down in the README.

### 3.6 Census API returns HTTP 200 on failure

Without a key it returns an **HTML page titled "Missing Key" with status 200**, not a 401.
Naive code will treat it as success and parse garbage. The extractor must validate the
response body, not just the status code. Key: https://api.census.gov/data/key_signup.html

### 3.7 Both API URLs redirect

`data.sfgov.org` → `data.sf.gov` (HTTP 301). `requests` follows redirects by default; `curl`
needs `-L`.

### 3.8 The GeoJSON is one FeatureCollection

41 features, `MultiPolygon` geometry, neighborhood name in property **`nhood`**, 1.7 MB.
It is a single object — if we ever load it into BigQuery it must be split to newline-delimited
first. GeoPandas reads it as-is.

---

## 4. Proposed data contract

Each source module exposes one function returning a DataFrame. Source type does not matter —
API, file, or scrape all end as a DataFrame.

### Maurice — `fetch_businesses() -> pd.DataFrame`

| Column | Type | From |
|---|---|---|
| `business_id` | str | `uniqueid` |
| `dba_name` | str | `dba_name` |
| `address` | str | `full_business_address` |
| `latitude` | float | `location.coordinates[1]` |
| `longitude` | float | `location.coordinates[0]` |
| `naics_code` | str | `self_reported_naics_code` |
| `lic_description` | str | `lic_code_description` |
| `sf_neighborhood` | str | `neighborhoods_analysis_boundaries` |

Filters applied at source: `city = 'San Francisco'`, `location_end_date IS NULL`,
`location IS NOT NULL`.

### Jessie — `fetch_census() -> pd.DataFrame`

| Column | Type | Note |
|---|---|---|
| `tract_geoid` | str | must join to a geography we can map |
| `total_population` | int | `B01003_001E` |

### Jonah — `fetch_neighborhoods() -> gpd.GeoDataFrame`

| Column | Type | From |
|---|---|---|
| `nhood` | str | property `nhood` |
| `geometry` | MultiPolygon | CRS must be **EPSG:4326** |

### Division of labour

- **Source owners:** extract, rename to the contract, drop invalid rows, basic type casting.
- **Unified ETL (me):** pet classification, neighborhood assignment, census reconciliation,
  cross-source validation, final metric, write to GCS.

Rationale: classification and joins are shared business logic. If three people each implement
their own version we get three different answers.

---

## 5. Pipeline stages

```python
businesses    = fetch_businesses()
census        = fetch_census()
neighborhoods = fetch_neighborhoods()

businesses = standardize(businesses)
pet        = classify_pet_businesses(businesses)      # NAICS / LIC rule
pet        = assign_neighborhood(pet, neighborhoods)  # or use existing column
pop        = reconcile_census_to_neighborhoods(census, neighborhoods)
final      = compute_metrics(pet, pop)                # per 1,000 residents

validate(final)
save_to_gcs(final)
```

---

## 6. Open decisions for the team

1. **Which source is the scraped one?** Canvas requires *"FastAPI code for scraping at least
   one data source."* We currently have two APIs and one file download. None is HTML scraping.
2. **Use `neighborhoods_analysis_boundaries` or do our own spatial join?**
3. **What is the pet-business classification rule?** Which NAICS / LIC codes count.
4. **How do Census tracts map to neighborhoods?** They do not nest cleanly.
5. **Raw and processed stored separately, or one output for now?**
6. **Confirm the final metric.**

---

## 7. The spatial join question

Our README says we will assign businesses to neighborhoods by matching coordinates to polygons.
But the business dataset **already has** `neighborhoods_analysis_boundaries`.

**Option A — use the existing column.** Free, no geometry handling, no CRS issues.
Risk: unknown null rate, and we are trusting SF's assignment.

**Option B — do our own spatial join** with GeoPandas `sjoin` (or `ST_CONTAINS` in BigQuery).
Demonstrates the technique and is defensible for the report. Costs CRS alignment and compute.

**Recommendation:** measure the null rate in that column first, then decide. If it is well
populated, use it as the primary and run our own join as a cross-check — that validation is
itself a good result for the report.

---

## 8. Known risks

| Risk | Impact | Mitigation |
|---|---|---|
| No scraped source | fails an explicit requirement | resolve at 2026-10-03 meeting |
| Pet classification rule is a judgement call | determines the entire result | document the rule in README |
| Tract → neighborhood mismatch | population denominator wrong | pick and state a rule |
| Census key missing | extractor silently returns HTML | validate body, key in `.env` |
| ~3% of SF businesses lack coordinates | small undercount | log and report the count |
| CRS mismatch | join silently produces zero matches | assert EPSG:4326 both sides |

---

## 9. Credentials

No keys in code. Census key in `.env` as `CENSUS_API_KEY`, listed in `.env_template`,
loaded with `python-dotenv`. GCP access via service account path in `GCP_SERVICE_ACCOUNT_KEY`.
Per the work contract, scripts take a `dev` flag selecting the `-dev` or `-prod` bucket.
