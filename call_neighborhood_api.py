


import requests
import geopandas as gpd


URL = "http://127.0.0.1:8000"


def upload_neighborhood():
    response = requests.get(URL+"/upload_neighborhood")
    print("upload_neighborhood response", response)

def download_neighborhood():
    response = requests.get(URL+"/download_neighborhood")
    print("download neighborhood response ", response)
    geojson_dict = response.json()
    geopandas_dataframe = gpd.GeoDataFrame.from_features(geojson_dict["features"], crs = "EPSG:4326")
    return geopandas_dataframe



if __name__ == '__main__':
    upload_neighborhood()
    df = download_neighborhood()
    print(df.head(10))