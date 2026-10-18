import io
import os

import geopandas as gpd
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from google.api_core.exceptions import NotFound
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from collectors import acs_data, neighborhood, registered_businesses

load_dotenv()

CATEGORIES = ["veterinary", "pet_care", "pet_store"]
RESULT_FILE = "processed/pet_care_by_neighborhood.csv"
# California Albers (meters), for distances when matching tracts to neighborhoods.
PROJECTED_CRS = "EPSG:3310"

# Bucket the endpoints use when dev_mode isn't passed. Local runs default to -dev;
# the Cloud Run deploy sets DEV_MODE=false so the deployed app serves -prod.
DEFAULT_DEV_MODE = os.getenv("DEV_MODE", "true").strip().lower() != "false"

# When ETL_INVOKER is set (on Cloud Run), POST /etl only accepts a Google-signed
# ID token for that service account with ETL_AUDIENCE as its audience. Unset
# locally, so /etl stays open for development.
ETL_INVOKER = os.getenv("ETL_INVOKER")
ETL_AUDIENCE = os.getenv("ETL_AUDIENCE")


def get_neighborhoods(dev_mode=True):
    return neighborhood.download_neighborhood(dev_mode)


def get_businesses(dev_mode=True):
    return registered_businesses.download_registered_businesses(dev_mode)


def get_census(dev_mode=True):
    df = acs_data.download_acs(dev_mode)
    df["population"] = pd.to_numeric(df["population"], errors="coerce")
    return df


def check_sources(businesses, neighborhoods, census):
    if businesses.empty or neighborhoods.empty or census.empty:
        raise ValueError("a source came back empty")
    missing = set(businesses["neighborhood"].dropna()) - set(neighborhoods["neighborhood"])
    if missing:
        raise ValueError(f"business neighborhoods with no boundary: {sorted(missing)}")
    if not {"lat", "lon"} <= set(census.columns):
        raise ValueError("census data has no lat/lon columns")


def population_by_neighborhood(census, neighborhoods):
    # Each tract goes to the neighborhood containing its center point. A few
    # shoreline tracts sit just outside every polygon, so match on the nearest
    # neighborhood instead: points inside a polygon are at distance 0.
    tracts = census.dropna(subset=["lat", "lon"])
    points = gpd.GeoDataFrame(
        tracts[["population"]],
        geometry=gpd.points_from_xy(tracts["lon"].astype(float), tracts["lat"].astype(float)),
        crs="EPSG:4326",
    ).to_crs(PROJECTED_CRS)
    hoods = neighborhoods[["neighborhood", "geometry"]]
    if hoods.crs is None:
        hoods = hoods.set_crs("EPSG:4326")
    matched = gpd.sjoin_nearest(points, hoods.to_crs(PROJECTED_CRS), how="left")
    matched = matched[~matched.index.duplicated()]
    return matched.groupby("neighborhood")["population"].sum()


def combine(businesses, neighborhoods, census):
    counts = pd.crosstab(businesses["neighborhood"], businesses["pet_category"])
    counts = counts.reindex(columns=CATEGORIES, fill_value=0)

    result = neighborhoods[["neighborhood"]].merge(
        counts, left_on="neighborhood", right_index=True, how="left"
    )
    result[CATEGORIES] = result[CATEGORIES].fillna(0).astype(int)
    result.insert(1, "businesses_total", result[CATEGORIES].sum(axis=1))

    population = population_by_neighborhood(census, neighborhoods)
    result["population"] = result["neighborhood"].map(population).fillna(0).astype(int)
    # Left empty for neighborhoods with no residents (e.g. parks).
    result["businesses_per_1000"] = (
        (result["businesses_total"] / result["population"] * 1000)
        .where(result["population"] > 0)
        .round(2)
    )
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
    check_sources(businesses, neighborhoods, census)
    result = combine(businesses, neighborhoods, census)
    saved_to = save_result(result, dev_mode)

    return {
        "neighborhoods": len(neighborhoods),
        "businesses": len(businesses),
        "census_tracts": len(census),
        "businesses_counted": int(result["businesses_total"].sum()),
        "population_counted": int(result["population"].sum()),
        "saved_to": saved_to,
    }


def check_etl_caller(authorization):
    if not ETL_INVOKER:
        return
    if not ETL_AUDIENCE:
        raise HTTPException(500, "ETL_AUDIENCE is not set")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        claims = id_token.verify_oauth2_token(
            authorization.removeprefix("Bearer "), google_requests.Request(), audience=ETL_AUDIENCE
        )
    except ValueError:
        raise HTTPException(401, "invalid token")
    if claims.get("email") != ETL_INVOKER or not claims.get("email_verified"):
        raise HTTPException(403, "caller may not run the ETL")


app = FastAPI(title="sf-pet-care API")


@app.exception_handler(NotFound)
def missing_data(request: Request, exc: NotFound):
    # A file missing from the bucket means no ETL run has filled it yet
    # (e.g. -prod right after the first deploy). Anything else, like a
    # missing bucket, is a real server error.
    if "No such object" in str(exc):
        return JSONResponse(status_code=503, content={"detail": "No data yet: run the ETL first"})
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


def as_json(df):
    return Response(df.to_json(orient="records"), media_type="application/json")


@app.get("/neighborhoods")
def neighborhoods(dev_mode: bool = DEFAULT_DEV_MODE):
    """SF neighborhood boundaries as GeoJSON (41 polygons)."""
    return Response(get_neighborhoods(dev_mode).to_json(), media_type="application/geo+json")


@app.get("/businesses")
def businesses(dev_mode: bool = DEFAULT_DEV_MODE):
    """Active pet-care businesses in SF: vets, pet care and pet stores."""
    return as_json(get_businesses(dev_mode))


@app.get("/census")
def census(dev_mode: bool = DEFAULT_DEV_MODE):
    """Census population and income for each SF census tract (ACS 5-year)."""
    return as_json(get_census(dev_mode))


@app.get("/processed")
def processed(dev_mode: bool = DEFAULT_DEV_MODE):
    """Pet-care business counts, population and businesses per 1,000 residents per neighborhood, from the last ETL run."""
    return as_json(get_result(dev_mode))


@app.post("/etl")
def etl(dev_mode: bool = DEFAULT_DEV_MODE, authorization: str | None = Header(default=None)):
    """Refresh all three sources, combine them per neighborhood, and save the result."""
    check_etl_caller(authorization)
    return run_etl(dev_mode)


if __name__ == "__main__":
    print(run_etl(DEFAULT_DEV_MODE))
