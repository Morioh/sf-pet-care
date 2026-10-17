import io
import os

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, Response

from collectors import acs_data, neighborhood, registered_businesses

load_dotenv()

CATEGORIES = ["veterinary", "pet_care", "pet_store"]
RESULT_FILE = "processed/pet_care_by_neighborhood.csv"


def get_neighborhoods(dev_mode=True):
    return neighborhood.download_neighborhood(dev_mode)


def get_businesses(dev_mode=True):
    return registered_businesses.download_registered_businesses(dev_mode)


def get_census(dev_mode=True):
    df = acs_data.download_acs(dev_mode)
    df["population"] = pd.to_numeric(df["population"], errors="coerce")
    return df


def check_sources(businesses, neighborhoods):
    if businesses.empty or neighborhoods.empty:
        raise ValueError("a source came back empty")
    missing = set(businesses["neighborhood"].dropna()) - set(neighborhoods["neighborhood"])
    if missing:
        raise ValueError(f"business neighborhoods with no boundary: {sorted(missing)}")


def combine(businesses, neighborhoods):
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
    client, bucket_name = neighborhood.get_client(dev_mode)
    data = client.bucket(bucket_name).blob(RESULT_FILE).download_as_bytes()
    return pd.read_csv(io.BytesIO(data))


def run_etl(dev_mode=True):
    if not os.getenv("CENSUS_API_KEY"):
        raise EnvironmentError("CENSUS_API_KEY is not set")

    neighborhood.upload_neighborhood(dev_mode)
    registered_businesses.upload_registered_businesses(dev_mode)
    acs_data.upload_acs(dev_mode)

    neighborhoods = get_neighborhoods(dev_mode)
    businesses = get_businesses(dev_mode)
    census = get_census(dev_mode)
    check_sources(businesses, neighborhoods)
    result = combine(businesses, neighborhoods)
    saved_to = save_result(result, dev_mode)

    return {
        "neighborhoods": len(neighborhoods),
        "businesses": len(businesses),
        "census_tracts": len(census),
        "businesses_counted": int(result["businesses_total"].sum()),
        "saved_to": saved_to,
    }


app = FastAPI(title="sf-pet-care API")


def as_json(df):
    return Response(df.to_json(orient="records"), media_type="application/json")


@app.get("/neighborhoods")
def neighborhoods(dev_mode: bool = True):
    """SF neighborhood boundaries as GeoJSON (41 polygons)."""
    return Response(get_neighborhoods(dev_mode).to_json(), media_type="application/geo+json")


@app.get("/businesses")
def businesses(dev_mode: bool = True):
    """Active pet-care businesses in SF: vets, pet care and pet stores."""
    return as_json(get_businesses(dev_mode))


@app.get("/census")
def census(dev_mode: bool = True):
    """Census population and income for each SF census tract (ACS 5-year)."""
    return as_json(get_census(dev_mode))


@app.get("/processed")
def processed(dev_mode: bool = True):
    """Pet-care business counts per neighborhood, by type, from the last ETL run."""
    return as_json(get_result(dev_mode))


@app.post("/etl")
def etl(dev_mode: bool = True):
    """Refresh all three sources, combine neighborhoods and businesses, and save the result."""
    return run_etl(dev_mode)


if __name__ == "__main__":
    print(run_etl())
