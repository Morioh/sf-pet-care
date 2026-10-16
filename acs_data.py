import json
import os
import pandas as pd
import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi import HTTPException
from google.oauth2 import service_account
from google.cloud import storage
app = FastAPI()

CENSUS_URL ="https://api.census.gov/data/2024/acs/acs5"


def get_client(dev_mode):
    load_dotenv()

    service_account_key = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
    project_id =os.getenv("GCP_PROJECT_ID") 
    bucket =os.getenv("GCP_BUCKET_NAME") 

    try:
        if not service_account_key:
            raise(ValueError("GCP_SERVICE_ACCOUNT_KEY is not set"))
        if not project_id:
            raise(ValueError("GCP_PROJECT_ID is not set"))
        if not bucket:
            raise(ValueError("GCP_BUCKET_NAME is not set."))   
    except ValueError as e:
        raise EnvironmentError(f"Missing a environment variable: {e}") from e
    if dev_mode:
        bucket += "-dev"
    else:
        bucket += "-prod"

    credentials = service_account.Credentials.from_service_account_file(
        service_account_key
    )

    client = storage.Client(project=project_id,
                            credentials=credentials)

    return  client,bucket



#let ACS tract data frrom the Cenus API
def download_data():
    load_dotenv()

    census_api_key =os.getenv("CENSUS_API_KEY")

    if not census_api_key:
        raise ValueError("CENSUS_API_KEY is missing from .env")

    params = {
        "get": "NAME,B01003_001E,B19013_001E",
        "for": "tract:*",
        "in": "state:06 county:075",
        "key": census_api_key
    }

    response = requests.get(CENSUS_URL,
                            params= params,
                            timeout = 38)


    response.raise_for_status()

    json_data =  response.json()
    return json_data



#clean Census data and keep the fields we need. 
def clean_data(raw_data):
    final_data = []
    rows = raw_data[1:]

    for i in rows:
        tract_name= i[0]
        population = int(i[1])
        income = i[2]
        state = i[3]
        county = i[4]
        tract = i[5]
        geoid = state+ county+tract

        real_income = None
        if income is None:
            real_income = None
        elif income =="-666666666":
            real_income = None
        elif income =="":
            real_income = None
        else:
            real_income = int(income)
        dataSet = dict()
        dataSet["geoid"] = geoid
        dataSet["tract_name"] = tract_name
        dataSet["population"] = population
        dataSet["income"] = real_income 
        final_data.append(dataSet)
    return final_data



def upload_to_gcs(data, dev_mode = True):
    client,bucket = get_client(dev_mode)

    bucket = client.bucket(bucket)
    filename = "acs.json"
    file = bucket.blob(filename)

    content = json.dumps(data)

    file.upload_from_string(content, content_type="application/json")



#downloads theacs.json and returns it
def download_acs(dev_mode = True):
    client, bucket = get_client(dev_mode)
    bucket = client.bucket(bucket)
    filename = "acs.json"
    file = bucket.blob(filename)

    bytes = file.download_as_bytes()
    data = json.loads(bytes)

    dataframe = pd.DataFrame(data)
    dataframe["geoid"] = dataframe["geoid"].astype(str)

    return dataframe



@app.post("/collect/acs")
def collect_acs():
    """
    Collect the ACS census tract datas for San Francisco,
    and clean it and upload the result to GCS.
    """

    try:
        raw_data = download_data()  
        cleaned_data = clean_data(raw_data)
 
        upload_to_gcs(cleaned_data, dev_mode = True)

        return {
            "message": "ACS data collected successfully",
            "records": len(cleaned_data),
            "file": "acs.json"
        }
    except requests.RequestException as e:
        raise HTTPException(

            status_code=502,
            detail=f"Census API request failed: {e}"
        )
    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=str(e)
        )



if __name__ == "__main__":
    raw_data = download_data()
    cleaned_data = clean_data(raw_data)

    print("Raw rows: ", len(raw_data) - 1)
    print("Cleaned rows: ", len(cleaned_data))
    print("First cleaned row: ", cleaned_data[0])