import pandas as pd
import httpx

import os

import json

from dotenv import load_dotenv
from google.oauth2 import service_account
from google.cloud import storage


DATASET_URL = "https://data.sfgov.org/resource/g8m3-pdis.json"
FILE_NAME = "registered_businesses.json"
PAGE_SIZE = 50_000  # Socrata's max rows per request

# Columns kept from the API (the full dataset has ~35 columns).
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
    "location_start_date",
    "self_reported_naics_code",
    "lic",
    "lic_code_description",
    "neighborhoods_analysis_boundaries",
    "supervisor_district",
    "location",
]

# NAICS codes are self-reported and sometimes truncated (e.g. "81291"),
# so categories are matched by prefix.
PET_NAICS_PREFIXES = {
    "veterinary": "54194",  # Veterinary services
    "pet_care": "81291",  # Pet care (grooming, boarding, walking)
    "pet_store": "45391",  # Pet and pet supplies stores
}

def get_client(dev_mode):
    load_dotenv()
    service_account_key = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
    project_id = os.getenv("GCP_PROJECT_ID")
    bucket_name = os.getenv("GCP_BUCKET_NAME")

    try:
        if not service_account_key:
            raise(ValueError("GCP_SERVICE_ACCOUNT_KEY is not set"))
        if not project_id:
            raise(ValueError("GCP_PROJECT_ID is not set"))
        if not bucket_name:
            raise(ValueError("GCP_BUCKET_NAME is not set"))
    except ValueError as e:
        raise EnvironmentError(f"Missing environment variable: {e}") from e

    if dev_mode:
        bucket_name += "-dev"
    else:
        bucket_name += "-prod"

    credentials = service_account.Credentials.from_service_account_file(service_account_key)
    client = storage.Client(project=project_id,
                            credentials=credentials)
    return client, bucket_name


#fetches active pet-care businesses in San Francisco (vets, pet care, pet stores)
def fetch_registered_businesses_data_from_api():
    load_dotenv()
    app_token = os.getenv("SODA_APP_TOKEN")  # optional; raises Socrata's rate limits
    headers = {"X-App-Token": app_token} if app_token else {}

    naics_filter = " OR ".join(
        f"starts_with(self_reported_naics_code, '{prefix}')" for prefix in PET_NAICS_PREFIXES.values()
    )
    where = ("dba_end_date IS NULL AND location_end_date IS NULL "
             f"AND city = 'San Francisco' AND ({naics_filter})")

    #page through the API until a short page comes back
    rows = []
    offset = 0
    while True:
        response = httpx.get(DATASET_URL,
                             params={"$select": ",".join(FIELDS),
                                     "$where": where,
                                     "$order": "uniqueid",  # stable order is required for paging
                                     "$limit": PAGE_SIZE,
                                     "$offset": offset},
                             headers=headers,
                             timeout=60,
                             follow_redirects=True)
        response.raise_for_status()
        page = response.json()
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    businesses_df = pd.DataFrame(rows, columns=FIELDS)

    #flatten the GeoJSON point into longitude/latitude columns
    coordinates = businesses_df["location"].apply(
        lambda loc: loc.get("coordinates", [None, None]) if isinstance(loc, dict) else [None, None]
    )
    businesses_df["longitude"] = coordinates.str[0]
    businesses_df["latitude"] = coordinates.str[1]
    businesses_df = businesses_df.drop(columns="location")

    #label each business with its pet category
    def categorize(naics):
        for category, prefix in PET_NAICS_PREFIXES.items():
            if isinstance(naics, str) and naics.startswith(prefix):
                return category
        return None
    businesses_df["pet_category"] = businesses_df["self_reported_naics_code"].apply(categorize)

    businesses_df = businesses_df.rename(columns={"neighborhoods_analysis_boundaries": "neighborhood"})
    businesses_df = businesses_df.sort_values(by=["neighborhood", "dba_name"])
    return businesses_df


def upload_registered_businesses(dev_mode = True):
    client, bucket_name = get_client(dev_mode)
    bucket = client.bucket(bucket_name)
    file = bucket.blob(FILE_NAME)

    businesses_df = fetch_registered_businesses_data_from_api()

    json_str = businesses_df.to_json(orient="records", indent=2)
    file.upload_from_string(json_str, content_type="application/json")


#downloads registered_businesses.json from GCP and returns it as a DataFrame
def download_registered_businesses(dev_mode = True):
    client, bucket_name = get_client(dev_mode)
    bucket = client.bucket(bucket_name)
    file = bucket.blob(FILE_NAME)
    data_bytes = file.download_as_bytes()
    #json.loads keeps code-like columns (zip, NAICS, IDs) as strings so leading zeros aren't lost
    dataframe = pd.DataFrame(json.loads(data_bytes))
    return dataframe
