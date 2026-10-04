

import geopandas as gpd

import os

import io

from dotenv import load_dotenv
from google.oauth2 import service_account
from google.cloud import storage


def get_client(dev_mode):
    load_dotenv()
    service_account_key = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
    project_id = os.getenv("GCP_PROJECT_ID")
    bucket_name = os.getenv("GCP_BUCKET_NAME")
    print("example")
    print(service_account_key)

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



def fetch_and_clean_neighborhood_data_from_internet():
    url = "https://data.sf.gov/api/v3/views/j2bu-swwd/query.geojson?accessType=DOWNLOAD"
    neighborhood_gdf = gpd.read_file(url)
    #select just neighborhood and geometry
    neighborhood_gdf = neighborhood_gdf[['nhood', 'geometry']]
    neighborhood_gdf  = neighborhood_gdf .rename(columns={"nhood": "neighborhood"})
    neighborhood_gdf = neighborhood_gdf.sort_values(by='neighborhood')
    return neighborhood_gdf


def upload_neighborhood(dev_mode = True):
    client, bucket_name = get_client(dev_mode)
    bucket = client.bucket(bucket_name)
    file_name = "neighborhood.geojson"
    file = bucket.blob(file_name)

    ngdf = fetch_and_clean_neighborhood_data_from_internet()

    geojson_str = ngdf.to_json(drop_id=True)
    file.upload_from_string(geojson_str, content_type="application/geo+json")



#downloads neighborhood.geoson from GCP and returns it as a DataFrame
def download_neighborhood(dev_mode = True):
    client, bucket_name = get_client(dev_mode)
    bucket = client.bucket(bucket_name)
    print(bucket_name)
    file_name = "neighborhood.geojson"
    file = bucket.blob(file_name)
    data_bytes = file.download_as_bytes()
    geo_dataframe = gpd.read_file(io.BytesIO(data_bytes))
    return geo_dataframe

