"""Unified ETL for sf-pet-care.

Combines the team's data sources into one processed dataset: pet-care
businesses per neighbourhood.

Run from the repo root:
    python -m etl.pipeline              # dev bucket
    python -m etl.pipeline --prod       # prod bucket
    python -m etl.pipeline --dry-run    # no bucket writes at all

Four functions, one per API endpoint:
    get_neighborhoods()  Jonah   SF website -> bucket -> table   (refreshed by run_etl)
    get_businesses()     Maurice SF API     -> bucket -> table   (refreshed by run_etl)
    get_census()         Jessie  bucket -> table  (load only: ACS changes yearly and
                                                    needs her Census key, so she uploads it)
    run_etl()            calls the three, validates, integrates, saves processed CSV

Each worker calls the OWNER'S OWN functions in sources/. Nothing is
reimplemented here, so a change on their side shows up as a real failure.
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
logging.getLogger("httpx").setLevel(logging.WARNING)  # hide per-request lines
log = logging.getLogger("etl")

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"  # repo root
CATEGORIES = ("veterinary", "pet_care", "pet_store")


def _source(name):
    """Import sources/<name>.py, or return None if it isn't there yet."""
    try:
        return importlib.import_module(f"sources.{name}")
    except ImportError as e:
        log.warning("source %s not available (%s)", name, e)
        return None


# ---------------------------------------------------------------------------
# The three source workers
# ---------------------------------------------------------------------------

def get_neighborhoods(dev_mode=True, dry_run=False, refresh=True):
    """Jonah: return the neighbourhood polygons.

    refresh=True  re-fetch from the website and overwrite the bucket file first
    refresh=False just read what is in the bucket (read-only API endpoint)
    dry_run=True  fetch from the website and return it, bucket untouched
    """
    nb = _source("neighborhood")
    if nb is None:
        raise RuntimeError("sources/neighborhood.py is missing")

    if dry_run:
        gdf = nb.fetch_and_clean_neighborhood_data_from_internet()
        log.info("neighborhoods: %d polygons fetched (DRY RUN, not uploaded)", len(gdf))
        return gdf

    if refresh:
        nb.upload_neighborhood(dev_mode)
    gdf = nb.download_neighborhood(dev_mode)
    log.info("neighborhoods: %d polygons (%s)", len(gdf),
             "refreshed in bucket" if refresh else "read from bucket")
    return gdf


def get_businesses(dev_mode=True, dry_run=False, refresh=True):
    """Maurice: return the pet businesses. Same switches as get_neighborhoods."""
    rb = _source("registered_businesses")
    if rb is None:
        raise RuntimeError("sources/registered_businesses.py is missing")

    if dry_run:
        df = rb.fetch_registered_businesses_data_from_api()
        log.info("businesses: %d rows fetched (DRY RUN, not uploaded)", len(df))
        return df

    if refresh:
        rb.upload_registered_businesses(dev_mode)
    df = rb.download_registered_businesses(dev_mode)
    log.info("businesses: %d rows (%s)", len(df),
             "refreshed in bucket" if refresh else "read from bucket")
    return df


def get_census(dev_mode=True):
    """Jessie: read acs.json from the bucket (she uploads it with her own key).

    Load only - no refresh. The Census API returns every value as text, so
    population is converted to a number here; her file stays unchanged.
    Returns None if her source file or acs.json is missing.
    """
    acs = _source("acs_data")
    if acs is None:
        return None
    try:
        df = acs.download_acs(dev_mode=dev_mode)
    except Exception as e:
        log.warning("census: could not read acs.json (%s) - continuing without it", type(e).__name__)
        return None

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


def run_etl(dev_mode=True, dry_run=False):
    """The boss: get all three sources, validate, integrate, save.

    This is what POST /etl (and Cloud Scheduler) calls.
    """
    neighborhoods = get_neighborhoods(dev_mode, dry_run)
    businesses = get_businesses(dev_mode, dry_run)
    census = get_census(dev_mode)

    validate_sources(businesses, neighborhoods, census)
    businesses, neighborhoods = standardize_sources(businesses, neighborhoods)
    result = integrate_sources(businesses, neighborhoods, census)
    target = save_output(result, dev_mode, dry_run)

    return {
        "neighborhoods": len(neighborhoods),
        "businesses": len(businesses),
        "census_tracts": None if census is None else len(census),
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
        summary = run_etl(dev_mode, args.dry_run)
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
