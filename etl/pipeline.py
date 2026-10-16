"""sf-pet-care ETL: pet-care businesses per San Francisco neighborhood.

Run from the repo root:
    python -m etl.pipeline

    get_neighborhoods()  Jonah's polygons from the bucket
    get_businesses()     Maurice's pet businesses from the bucket
    get_census()         Jessie's census tracts from the bucket
    run_etl()            refresh the sources, combine them, save the result

Each function calls the owner's own code in sources/.
"""

import io

import pandas as pd

from sources import acs_data, neighborhood, registered_businesses

CATEGORIES = ["veterinary", "pet_care", "pet_store"]
RESULT_FILE = "processed/pet_care_by_neighborhood.csv"


def get_neighborhoods(dev_mode=True):
    return neighborhood.download_neighborhood(dev_mode)


def get_businesses(dev_mode=True):
    return registered_businesses.download_registered_businesses(dev_mode)


def get_census(dev_mode=True):
    """Jessie uploads acs.json with her Census key; we only read it.
    The Census API sends numbers as text, so population is converted."""
    df = acs_data.download_acs(dev_mode)
    df["population"] = pd.to_numeric(df["population"], errors="coerce")
    return df


def check_sources(businesses, neighborhoods):
    """Stop before combining if the two tables can't be joined correctly."""
    if businesses.empty or neighborhoods.empty:
        raise ValueError("a source came back empty")
    missing = set(businesses["neighborhood"].dropna()) - set(neighborhoods["neighborhood"])
    if missing:
        raise ValueError(f"business neighborhoods with no boundary: {sorted(missing)}")


def combine(businesses, neighborhoods):
    """One row per neighborhood: how many businesses of each type."""
    counts = pd.crosstab(businesses["neighborhood"], businesses["pet_category"])
    counts = counts.reindex(columns=CATEGORIES, fill_value=0)

    result = neighborhoods[["neighborhood"]].merge(
        counts, left_on="neighborhood", right_index=True, how="left"
    )
    result[CATEGORIES] = result[CATEGORIES].fillna(0).astype(int)
    result.insert(1, "businesses_total", result[CATEGORIES].sum(axis=1))
    return result.sort_values("businesses_total", ascending=False).reset_index(drop=True)


def save_result(result, dev_mode=True):
    client, bucket_name = neighborhood.get_client(dev_mode)
    client.bucket(bucket_name).blob(RESULT_FILE).upload_from_string(
        result.to_csv(index=False), content_type="text/csv"
    )
    return f"gs://{bucket_name}/{RESULT_FILE}"


def get_result(dev_mode=True):
    """Read the saved result back (for the API's /processed endpoint)."""
    client, bucket_name = neighborhood.get_client(dev_mode)
    data = client.bucket(bucket_name).blob(RESULT_FILE).download_as_bytes()
    return pd.read_csv(io.BytesIO(data))


def run_etl(dev_mode=True):
    # 1. refresh the raw files from the websites (census is uploaded by Jessie)
    neighborhood.upload_neighborhood(dev_mode)
    registered_businesses.upload_registered_businesses(dev_mode)

    # 2. read them back, check, combine, save
    neighborhoods = get_neighborhoods(dev_mode)
    businesses = get_businesses(dev_mode)
    check_sources(businesses, neighborhoods)
    result = combine(businesses, neighborhoods)
    saved_to = save_result(result, dev_mode)

    return {
        "neighborhoods": len(neighborhoods),
        "businesses": len(businesses),
        "businesses_counted": int(result["businesses_total"].sum()),
        "saved_to": saved_to,
    }


if __name__ == "__main__":
    print(run_etl())
    print(get_result().head(10).to_string(index=False))
