"""Unified ETL for sf-pet-care.

Combines the team's data sources into one processed dataset: pet-care
businesses per neighbourhood, normalised by population.

Run from the repo root:
    python -m etl.pipeline              # dev bucket
    python -m etl.pipeline --prod       # prod bucket
    python -m etl.pipeline --dry-run    # no bucket writes at all

Flow:  acquire  -> internet -> RAW files in the bucket   (each owner's upload_*)
       load     -> RAW files in the bucket -> DataFrames (each owner's download_*)
       validate -> integrate -> save PROCESSED csv to the bucket

Each source sits behind a small adapter that calls the OWNER'S OWN function
in sources/. Nothing is reimplemented here, so a change on their side shows
up as a real failure rather than silently diverging logic. A source whose
file is missing is skipped with a warning instead of crashing the run.

Acquisition wiring (2026-10-16):
    Jonah    sources/neighborhood.py           upload_neighborhood      WIRED
    Maurice  sources/registered_businesses.py  upload_registered_...    WIRED
    Jessie   sources/acs_data.py               upload_acs               WIRED
"""

import argparse
import importlib
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("etl")

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"  # repo root
CATEGORIES = ("veterinary", "pet_care", "pet_store")


def _source(name):
    """Import sources/<name>.py, or return None if it isn't there yet.

    Lets the pipeline run with whatever sources are ready: a missing file is
    a warning, not a crash.
    """
    try:
        return importlib.import_module(f"sources.{name}")
    except ImportError as e:
        log.warning("source %s not available (%s) - skipped", name, e)
        return None


# ---------------------------------------------------------------------------
# Step 1 - acquire: internet -> RAW files in the bucket
# ---------------------------------------------------------------------------

def acquire(dev_mode=True, dry_run=False):
    """Refresh each wired source's raw file in the bucket.

    dry_run fetches from the internet (proves the source is reachable and
    parses) but uploads nothing.
    """
    done = {}

    nb = _source("neighborhood")  # Jonah
    if nb:
        if dry_run:
            gdf = nb.fetch_and_clean_neighborhood_data_from_internet()
            log.info("acquire neighborhood: fetched %d polygons (DRY RUN, not uploaded)", len(gdf))
            done["neighborhood"] = f"fetched {len(gdf)} (dry run)"
        else:
            nb.upload_neighborhood(dev_mode)
            log.info("acquire neighborhood: uploaded neighborhood.geojson")
            done["neighborhood"] = "uploaded"

    rb = _source("registered_businesses")  # Maurice
    if rb:
        if dry_run:
            df = rb.fetch_registered_businesses_data_from_api()
            log.info("acquire businesses: fetched %d rows (DRY RUN, not uploaded)", len(df))
            done["registered_businesses"] = f"fetched {len(df)} (dry run)"
        else:
            rb.upload_registered_businesses(dev_mode)
            log.info("acquire businesses: uploaded registered_businesses.json")
            done["registered_businesses"] = "uploaded"

    acs = _source("acs_data")  # Jessie
    if acs:
        if dry_run:
            df = acs.clean_acs_data(os.getenv("CENSUS_API_KEY"))
            log.info("acquire census: fetched %d tracts (DRY RUN, not uploaded)", len(df))
            done["acs"] = f"fetched {len(df)} (dry run)"
        else:
            acs.upload_acs(dev_mode)
            log.info("acquire census: uploaded acs.json")
            done["acs"] = "uploaded"

    return done


# ---------------------------------------------------------------------------
# Step 2 - load: RAW files in the bucket -> DataFrames
# ---------------------------------------------------------------------------

def load_businesses(dev_mode=True):
    """Maurice's pet-care businesses.

    Reads the bucket. Without credentials, falls back to his own API fetch so
    the pipeline is still testable -- both are his functions, same columns.
    """
    rb = _source("registered_businesses")
    if rb is None:
        raise RuntimeError("businesses source missing - cannot integrate without it")

    try:
        df = rb.download_registered_businesses(dev_mode=dev_mode)
        log.info("businesses: %d rows from the bucket", len(df))
    except Exception as e:
        log.warning("bucket read failed (%s); using Maurice's API fetch", type(e).__name__)
        df = rb.fetch_registered_businesses_data_from_api()
        log.info("businesses: %d rows from the SF API", len(df))
    return df


def load_neighborhoods(dev_mode=True):
    """Jonah's neighbourhood polygons, same fallback pattern."""
    nb = _source("neighborhood")
    if nb is None:
        raise RuntimeError("neighborhood source missing - cannot integrate without it")

    try:
        gdf = nb.download_neighborhood(dev_mode=dev_mode)
        log.info("neighborhoods: %d polygons from the bucket", len(gdf))
    except Exception as e:
        log.warning("bucket read failed (%s); using Jonah's source fetch", type(e).__name__)
        gdf = nb.fetch_and_clean_neighborhood_data_from_internet()
        log.info("neighborhoods: %d polygons from the source URL", len(gdf))
    return gdf


def load_acs(dev_mode=True):
    """Jessie's census tracts from the bucket.

    The Census API returns every value as text, so population is converted
    to a number here (her function stays unchanged).
    """
    acs = _source("acs_data")
    if acs is None:
        return None

    df = acs.download_acs(dev_mode=dev_mode)
    df["geoid"] = df["geoid"].astype(str)
    df["population"] = pd.to_numeric(df["population"], errors="coerce").astype("Int64")
    log.info("census: %d tracts from the bucket", len(df))
    return df


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def validate_sources(businesses, neighborhoods, census=None):
    """Fail loudly before the join, not silently after it."""
    from etl import contract

    contract.check_businesses(businesses)
    contract.check_neighborhoods(neighborhoods)
    contract.check_join(businesses, neighborhoods)
    if census is not None:
        contract.check_census(census)


def standardize_sources(businesses, neighborhoods):
    """Both sources already name the key `neighborhood`; just trim whitespace."""
    b, n = businesses.copy(), neighborhoods.copy()
    for df in (b, n):
        df["neighborhood"] = df["neighborhood"].astype("string").str.strip()

    blank = b["neighborhood"].isna().sum()
    if blank:
        log.warning("standardize: %d business(es) have no neighbourhood -- excluded", blank)
    return b, n


def integrate_sources(businesses, neighborhoods, census=None):
    """One row per neighbourhood, with counts per pet category.

    Joined on the neighbourhood NAME, not geometry: SF already assigns every
    business to an analysis neighbourhood and all values match Jonah's
    boundaries exactly. A spatial join would reproduce that column at the cost
    of a geometry dependency, and would recover none of the unplaced rows --
    they have no coordinates either.
    """
    placed = businesses.dropna(subset=["neighborhood"])

    counts = (
        placed.groupby("neighborhood", observed=True)
        .agg(businesses_total=("uniqueid", "nunique"))
        .reset_index()
    )
    for cat in CATEGORIES:
        per_cat = (
            placed[placed["pet_category"] == cat]
            .groupby("neighborhood", observed=True)["uniqueid"]
            .nunique()
            .rename(cat)
            .reset_index()
        )
        counts = counts.merge(per_cat, on="neighborhood", how="left")

    out = neighborhoods[["neighborhood"]].merge(counts, on="neighborhood", how="left")
    for col in ("businesses_total",) + CATEGORIES:
        out[col] = out[col].fillna(0).astype(int)

    # Census is loaded and validated, but tracts (GEOID) and neighbourhoods
    # (name) share no key. Until the team picks a tract->neighbourhood
    # mapping (docs/etl-design.md section 5), population stays empty.
    out["population"] = pd.NA
    out["businesses_per_1k"] = pd.NA
    if census is None:
        log.warning("integrate: no census source -- population and per-1k left empty")
    else:
        log.warning("integrate: census loaded (%d tracts) but no tract->neighbourhood "
                    "mapping yet -- population and per-1k left empty", len(census))

    return out.sort_values("businesses_total", ascending=False).reset_index(drop=True)


def save_output(df, dev_mode=True, dry_run=False):
    """Write the processed dataset to the bucket, or to disk.

    dry_run writes locally and never touches the bucket. The team shares one
    -dev bucket, so a test that uploads on every run litters it with
    near-identical files. The output is still written so counts can be checked.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"pet_care_by_neighborhood_{stamp}.csv"
    bucket = os.getenv("GCP_BUCKET_NAME")
    key = os.getenv("GCP_SERVICE_ACCOUNT_KEY")

    if dry_run:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        path = PROCESSED_DIR / name
        df.to_csv(path, index=False)
        log.info("DRY RUN: wrote %s - bucket untouched", path)
        return str(path)

    if bucket and key:
        from google.cloud import storage
        from google.oauth2 import service_account

        suffix = "-dev" if dev_mode else "-prod"
        creds = service_account.Credentials.from_service_account_file(key)
        client = storage.Client(project=os.getenv("GCP_PROJECT_ID"), credentials=creds)
        client.bucket(bucket + suffix).blob(f"processed/{name}").upload_from_string(
            df.to_csv(index=False), content_type="text/csv"
        )
        uri = f"gs://{bucket}{suffix}/processed/{name}"
        log.info("saved: %s", uri)
        return uri

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / name
    df.to_csv(path, index=False)
    log.warning("saved LOCALLY (no GCP_SERVICE_ACCOUNT_KEY): %s", path)
    return str(path)


def run(dev_mode=True, dry_run=False):
    """Full pipeline: acquire -> load -> validate -> integrate -> save.

    This is what the API's /run-etl endpoint will call.
    """
    acquired = acquire(dev_mode, dry_run)

    businesses = load_businesses(dev_mode)
    neighborhoods = load_neighborhoods(dev_mode)
    census = load_acs(dev_mode)

    validate_sources(businesses, neighborhoods, census)
    businesses, neighborhoods = standardize_sources(businesses, neighborhoods)
    result = integrate_sources(businesses, neighborhoods, census)
    target = save_output(result, dev_mode, dry_run)

    return {
        "acquired": acquired,
        "neighborhoods": len(result),
        "businesses_placed": int(result["businesses_total"].sum()),
        "output": target,
        "_result": result,
    }


def main():
    ap = argparse.ArgumentParser(description="sf-pet-care unified ETL")
    ap.add_argument("--prod", action="store_true", help="use the -prod bucket")
    ap.add_argument("--dry-run", action="store_true",
                    help="write output locally only; never upload to the bucket")
    args = ap.parse_args()
    dev_mode = not args.prod
    log.info("=== sf-pet-care ETL starting (dev_mode=%s%s) ===",
             dev_mode, ", DRY RUN" if args.dry_run else "")

    try:
        summary = run(dev_mode, args.dry_run)
    except Exception:
        log.exception("ETL FAILED")
        return 1
    result, target = summary.pop("_result"), summary["output"]

    log.info("=== done: %d neighbourhoods -> %s ===", len(result), target)
    print()
    print(result.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
