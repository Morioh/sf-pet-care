"""FastAPI service for collecting San Francisco Registered Business Locations.

Source: San Francisco Registered Business Locations (Socrata SODA API, public).

Run locally:
    pip install -r requirements.txt
    fastapi dev registered_businesses.py    # development, auto-reloads on save
    fastapi run registered_businesses.py    # production-style (e.g. on a server)
Then open http://127.0.0.1:8000/docs for the interactive API docs.

All settings come from .env (copy .env.example to start):
    SODA_DATASET_URL  required; dataset endpoint
    SODA_APP_TOKEN    optional; raises Socrata's rate limits
                      (free token from https://data.sf.gov/profile/edit/developer_settings)
    GCS_BUCKET        optional; bucket for /collect/pet-care output. Empty = save to data/raw/
    GCP_PROJECT_ID    required when GCS_BUCKET is set
    GCS_PREFIX        optional; folder inside the bucket. Empty = bucket root
For GCS uploads, authenticate once with `gcloud auth application-default login`.
"""

import csv
import io
import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

load_dotenv(Path(__file__).parent / ".env")  # real env vars still take precedence


def env(name: str, required: bool = False) -> str:
    value = os.getenv(name, "").strip()
    if required and not value:
        raise RuntimeError(f"{name} is not set. Add it to .env (see .env.example).")
    return value


DATASET_URL = env("SODA_DATASET_URL", required=True)
APP_TOKEN = env("SODA_APP_TOKEN")
GCS_BUCKET = env("GCS_BUCKET")
GCP_PROJECT_ID = env("GCP_PROJECT_ID", required=bool(GCS_BUCKET))
GCS_PREFIX = env("GCS_PREFIX").strip("/")
DATA_DIR = Path(__file__).parent / "data" / "raw"
PAGE_SIZE = 50_000  # Socrata's max rows per request
TIMEOUT = httpx.Timeout(60.0)

# Columns kept in responses/exports (the full dataset has ~35 columns).
FIELDS = [
    "uniqueid",
    "ttxid",
    "certificate_number",
    "dba_name",
    "ownership_name",
    "full_business_address",
    "city",
    "state",
    "business_zip",
    "dba_start_date",
    "dba_end_date",
    "location_start_date",
    "location_end_date",
    "self_reported_naics_code",
    "lic",
    "lic_code_description",
    "neighborhoods_analysis_boundaries",
    "supervisor_district",
    "location",
]


class PetCategory(str, Enum):
    veterinary = "veterinary"
    pet_care = "pet_care"
    pet_store = "pet_store"


# NAICS codes are self-reported and sometimes truncated (e.g. "81291"),
# so categories are matched by prefix.
PET_NAICS_PREFIXES = {
    PetCategory.veterinary: ["54194"],  # Veterinary services
    PetCategory.pet_care: ["81291"],  # Pet care (grooming, boarding, walking)
    PetCategory.pet_store: ["45391"],  # Pet and pet supplies stores
}

app = FastAPI(
    title="SF Pet Care - Business Locations",
    description="Collects San Francisco registered business locations, "
    "with helpers for pet-care businesses.",
    version="0.1.0",
)


# ---------- helpers ----------


def soql_str(value: str) -> str:
    """Quote a value for use inside a SoQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def build_where(
    active_only: bool,
    sf_only: bool,
    neighborhood: str | None = None,
    naics_prefixes: list[str] | None = None,
    name_contains: str | None = None,
) -> str:
    clauses = []
    if active_only:
        clauses.append("dba_end_date IS NULL AND location_end_date IS NULL")
    if sf_only:
        clauses.append("city = 'San Francisco'")
    if neighborhood:
        clauses.append(f"neighborhoods_analysis_boundaries = {soql_str(neighborhood)}")
    if naics_prefixes:
        ors = " OR ".join(
            f"starts_with(self_reported_naics_code, {soql_str(p)})" for p in naics_prefixes
        )
        clauses.append(f"({ors})")
    if name_contains:
        clauses.append(f"upper(dba_name) like {soql_str('%' + name_contains.upper() + '%')}")
    return " AND ".join(clauses)


def pet_prefixes(category: PetCategory | None) -> list[str]:
    if category:
        return PET_NAICS_PREFIXES[category]
    return [p for prefixes in PET_NAICS_PREFIXES.values() for p in prefixes]


def categorize(naics: str | None) -> str | None:
    for category, prefixes in PET_NAICS_PREFIXES.items():
        if naics and any(naics.startswith(p) for p in prefixes):
            return category.value
    return None


async def soda_get(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Send one request to the SODA API."""
    headers = {"X-App-Token": APP_TOKEN} if APP_TOKEN else {}
    params = {k: v for k, v in params.items() if v not in (None, "")}
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(DATASET_URL, params=params, headers=headers)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(502, f"SODA API error: {e.response.text[:500]}") from e
        except httpx.RequestError as e:
            raise HTTPException(502, f"Could not reach SODA API: {e}") from e
    return resp.json()


async def fetch_all(where: str) -> list[dict[str, Any]]:
    """Fetch every matching row, paging through the API."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await soda_get(
            {
                "$select": ",".join(FIELDS),
                "$where": where,
                "$order": "uniqueid",  # stable order is required for paging
                "$limit": PAGE_SIZE,
                "$offset": offset,
            }
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def flatten(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten the GeoJSON point into latitude/longitude columns."""
    out = {k: row.get(k) for k in FIELDS if k != "location"}
    coords = (row.get("location") or {}).get("coordinates") or [None, None]
    out["longitude"], out["latitude"] = coords[0], coords[1]
    out["pet_category"] = categorize(row.get("self_reported_naics_code"))
    return out


def to_csv(rows: list[dict[str, Any]]) -> str:
    flat = [flatten(r) for r in rows]
    if not flat:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(flat[0].keys()))
    writer.writeheader()
    writer.writerows(flat)
    return buf.getvalue()


def upload_to_gcs(files: dict[str, tuple[str, str]]) -> dict[str, str]:
    """Upload {object_name: (content, content_type)} to GCS_BUCKET."""
    from google.cloud import storage  # imported lazily so local-only use needs no GCP setup

    bucket = storage.Client(project=GCP_PROJECT_ID).bucket(GCS_BUCKET)
    uris = {}
    for name, (content, content_type) in files.items():
        bucket.blob(name).upload_from_string(content, content_type=content_type)
        uris[name.rsplit(".", 1)[-1]] = f"gs://{GCS_BUCKET}/{name}"
    return uris


def save_local(files: dict[str, tuple[str, str]]) -> dict[str, str]:
    paths = {}
    for name, (content, _) in files.items():
        path = DATA_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        paths[name.rsplit(".", 1)[-1]] = str(path)
    return paths


async def save(rows: list[dict[str, Any]], name: str) -> dict[str, str]:
    """Save rows as JSON (raw) + CSV (flattened).

    Objects are written as [<prefix>/]<name>_<timestamp>.{json,csv}; the timestamp
    keeps each collection run separate.
    """
    base = f"{name}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    files = {
        f"{base}.json": (json.dumps(rows), "application/json"),
        f"{base}.csv": (to_csv(rows), "text/csv"),
    }
    if not GCS_BUCKET:
        return save_local(files)
    if GCS_PREFIX:
        files = {f"{GCS_PREFIX}/{k}": v for k, v in files.items()}
    try:
        # The GCS client is blocking, so keep it off the event loop.
        return await run_in_threadpool(upload_to_gcs, files)
    except Exception as e:
        raise HTTPException(502, f"Upload to gs://{GCS_BUCKET} failed: {e}") from e


# ---------- endpoints ----------


@app.get("/health")
async def health():
    storage = f"gs://{GCS_BUCKET}/{GCS_PREFIX}" if GCS_BUCKET else str(DATA_DIR)
    return {"status": "ok", "storage": storage}


@app.get("/businesses")
async def list_businesses(
    active_only: bool = Query(True, description="Exclude businesses/locations that have closed"),
    sf_only: bool = Query(True, description="Only locations with city = San Francisco"),
    neighborhood: str | None = Query(None, description="e.g. 'Mission', 'Noe Valley'"),
    naics_prefix: str | None = Query(None, description="e.g. '5419' or '812910'"),
    name_contains: str | None = Query(None, description="Case-insensitive match on business name"),
    limit: int = Query(100, ge=1, le=PAGE_SIZE),
    offset: int = Query(0, ge=0),
):
    """Return one page of registered business locations, filtered as requested."""
    where = build_where(
        active_only,
        sf_only,
        neighborhood,
        [naics_prefix] if naics_prefix else None,
        name_contains,
    )
    rows = await soda_get(
        {
            "$select": ",".join(FIELDS),
            "$where": where,
            "$order": "uniqueid",
            "$limit": limit,
            "$offset": offset,
        }
    )
    return {"count": len(rows), "offset": offset, "results": [flatten(r) for r in rows]}


@app.get("/businesses/pet-care")
async def list_pet_care(
    category: PetCategory | None = Query(None, description="Leave empty for all pet categories"),
    active_only: bool = True,
    neighborhood: str | None = None,
):
    """Return all pet-care businesses (veterinary, pet care services, pet stores) in SF."""
    where = build_where(active_only, True, neighborhood, pet_prefixes(category))
    rows = await fetch_all(where)
    return {"count": len(rows), "results": [flatten(r) for r in rows]}


@app.get("/businesses/pet-care/by-neighborhood")
async def pet_care_by_neighborhood(
    category: PetCategory | None = None,
    active_only: bool = True,
):
    """Count pet-care businesses per SF Analysis Neighborhood."""
    where = build_where(active_only, True, naics_prefixes=pet_prefixes(category))
    rows = await soda_get(
        {
            "$select": "neighborhoods_analysis_boundaries AS neighborhood, count(*) AS businesses",
            "$where": where,
            "$group": "neighborhoods_analysis_boundaries",
            "$order": "businesses DESC",
            "$limit": 1000,
        }
    )
    for r in rows:
        r["businesses"] = int(r["businesses"])
    return {"category": category or "all", "results": rows}


@app.post("/collect/pet-care")
async def collect_pet_care(active_only: bool = True):
    """Download all SF pet-care businesses and save them as JSON + CSV (GCS or data/raw/)."""
    rows = await fetch_all(build_where(active_only, True, naics_prefixes=pet_prefixes(None)))
    return {"rows": len(rows), "files": await save(rows, "pet_care_businesses")}
