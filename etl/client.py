"""Unified ETL for sf-pet-care.

Combines the team's data sources into one processed dataset: pet-care
businesses per neighbourhood, normalised by population.

    python client.py            # dev bucket
    python client.py --prod     # prod bucket

Each source sits behind a small adapter that calls the OWNER'S OWN function.
Nothing is reimplemented here, so a change on their side shows up as a real
failure rather than silently diverging logic.

Interfaces as of 2026-10-04:
    Maurice  download_registered_businesses(dev_mode) -> DataFrame   ready
    Jonah    download_neighborhood(dev_mode)          -> GeoDataFrame ready
    Jessie   not yet a DataFrame function                            NOT WIRED
"""

import argparse
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

PROCESSED_DIR = Path(__file__).parent / "data" / "processed"
CATEGORIES = ("veterinary", "pet_care", "pet_store")


# ---------------------------------------------------------------------------
# Source adapters
# ---------------------------------------------------------------------------

def load_businesses(dev_mode=True):
    """Maurice's pet-care businesses.

    Reads the bucket. Without credentials, falls back to his own API fetch so
    the pipeline is still testable -- both are his functions, same columns.
    """
    import registered_businesses as rb

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
    import neighborhood as nb

    try:
        gdf = nb.download_neighborhood(dev_mode=dev_mode)
        log.info("neighborhoods: %d polygons from the bucket", len(gdf))
    except Exception as e:
        log.warning("bucket read failed (%s); using Jonah's source fetch", type(e).__name__)
        gdf = nb.fetch_and_clean_neighborhood_data_from_internet()
        log.info("neighborhoods: %d polygons from the source URL", len(gdf))
    return gdf


def load_acs(dev_mode=True):
    """Jessie's census data -- NOT WIRED YET.

    TODO(jessie): when download_acs(dev_mode) exists, this becomes:
        import acs_data
        return acs_data.download_acs(dev_mode=dev_mode)

    Returns None rather than inventing data. The population denominator is
    simply absent from the output until her function is real.
    """
    log.warning("census: SKIPPED -- no DataFrame function on Jessie's branch yet")
    return None


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def validate_sources(businesses, neighborhoods):
    """Fail loudly before the join, not silently after it."""
    import contract

    contract.check_businesses(businesses)
    contract.check_neighborhoods(neighborhoods)
    contract.check_join(businesses, neighborhoods)


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

    if census is None:
        out["population"] = pd.NA
        out["businesses_per_1k"] = pd.NA
        log.warning("integrate: no census source -- population and per-1k left empty")
    else:
        raise NotImplementedError(
            "Census merge needs a tract->neighbourhood mapping; see docs/etl-design.md section 5"
        )

    return out.sort_values("businesses_total", ascending=False).reset_index(drop=True)


def save_output(df, dev_mode=True):
    """Write the processed dataset to the bucket, or to disk without credentials."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"pet_care_by_neighborhood_{stamp}.csv"
    bucket = os.getenv("GCP_BUCKET_NAME")
    key = os.getenv("GCP_SERVICE_ACCOUNT_KEY")

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


def main():
    ap = argparse.ArgumentParser(description="sf-pet-care unified ETL")
    ap.add_argument("--prod", action="store_true", help="use the -prod bucket")
    dev_mode = not ap.parse_args().prod
    log.info("=== sf-pet-care ETL starting (dev_mode=%s) ===", dev_mode)

    try:
        businesses = load_businesses(dev_mode)
        neighborhoods = load_neighborhoods(dev_mode)
        census = load_acs(dev_mode)

        validate_sources(businesses, neighborhoods)
        businesses, neighborhoods = standardize_sources(businesses, neighborhoods)
        result = integrate_sources(businesses, neighborhoods, census)
        target = save_output(result, dev_mode)
    except Exception:
        log.exception("ETL FAILED")
        return 1

    log.info("=== done: %d neighbourhoods -> %s ===", len(result), target)
    print()
    print(result.head(10).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
