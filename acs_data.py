import json
import os
from datetime import datetime

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from google.cloud import storage


load_dotenv(".env")

app = FastAPI()

CENSUS_URL = "https://api.census.gov/data/2024/acs/acs5"

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GCP_DEV_BUCKET = os.getenv("GCP_DEV_BUCKET")
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY")


def download_acs_data():
    if not CENSUS_API_KEY:
        raise ValueError("CENSUS_API_KEY is missing from .env")

    params = {
        "get": "NAME,B01003_001E,B19013_001E",
        "for": "tract:*",
        "in": "state:06 county:075",
        "key": CENSUS_API_KEY,
    }

    response = requests.get(
        CENSUS_URL,
        params=params,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


def clean_acs_data(raw_data):
    headers = raw_data[0]
    rows = raw_data[1:]

    cleaned_data = []

    for row in rows:
        record = dict(zip(headers, row))

        income_value = record["B19013_001E"]

        if income_value in (None, "", "-666666666"):
            median_income = None
        else:
            median_income = int(income_value)

        cleaned_record = {
            "geoid": (
                record["state"]
                + record["county"]
                + record["tract"]
            ),
            "tract_name": record["NAME"],
            "population": int(record["B01003_001E"]),
            "median_household_income": median_income,
        }

        cleaned_data.append(cleaned_record)

    return cleaned_data


def upload_to_gcs(data, file_name):
    client = storage.Client(project=GCP_PROJECT_ID)

    bucket = client.bucket(GCP_DEV_BUCKET)
    blob = bucket.blob(file_name)

    blob.upload_from_string(
        json.dumps(data, indent=2),
        content_type="application/json",
    )


@app.post("/collect/acs")
def collect_acs():
    try:
        raw_data = download_acs_data()
        cleaned_data = clean_acs_data(raw_data)

        today = datetime.now().strftime("%Y-%m-%d")

        file_name = (
            f"acs_sf_tracts/"
            f"acs_sf_tracts_{today}.json"
        )

        upload_to_gcs(cleaned_data, file_name)

        return {
            "message": "ACS data collected successfully",
            "records": len(cleaned_data),
            "file": file_name,
        }

    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Census API request failed: {error}",
        )

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        )


if __name__ == "__main__":
    raw_data = download_acs_data()
    cleaned_data = clean_acs_data(raw_data)

    print("Raw rows:", len(raw_data) - 1)
    print("Cleaned rows:", len(cleaned_data))
    print("First cleaned row:", cleaned_data[0])