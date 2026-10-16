



import json
import os 
import pandas as pd
import requests 
from dotenv import load_dotenv
from google.oauth2 import service_account
from google.cloud import storage



CENSUS_URL = "https://api.census.gov/data/2024/acs/acs5"



def get_client(dev_mode):
    load_dotenv()
    service_account_key = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
    project_id = os.getenv("GCP_PROJECT_ID")
    bucket_name = os.getenv("GCP_BUCKET_NAME")
    census_api = os.getenv("CENSUS_API_KEY")

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
    return client, bucket_name, census_api


def fetch_acs_data(census_api):
    params = {}
    params["get"] =  "NAME,B01003_001E,B19013_001E"
    params["for"] =  "tract:*"
    params["in"] = "state:06 county:075"
    params["key"] = census_api
    response = requests.get(CENSUS_URL, params=params, timeout=30)
    return response.json()


def clean_acs_data(census_api):
    data_as_list = fetch_acs_data(census_api)
    df = pd.DataFrame(data_as_list[1:], columns=data_as_list[0])
    df.rename(columns={'NAME': 'tract_name', 'B01003_001E':'population', 'B19013_001E': 'income'}, inplace = True)
    df.replace("-666666666", None, inplace=True)
    df['geoid'] = df['state'] + df['county'] + df['tract']
    return df

def upload_acs(dev_mode = True):
    client, bucket_name, census_api = get_client(dev_mode)
    bucket = client.bucket(bucket_name)
    file_name = "acs.json"
    file = bucket.blob(file_name)
    df = clean_acs_data(census_api)
    json_str = df.to_json(orient="records")
    file.upload_from_string(json_str, content_type="application/json")
    

def download_acs(dev_mode = True):
    client, bucket_name, census_api = get_client(dev_mode)
    bucket = client.bucket(bucket_name)
    file_name = "acs.json"
    file = bucket.blob(file_name)
    data_bytes = file.download_as_bytes()
    data = json.loads(data_bytes)
    return pd.DataFrame(data)