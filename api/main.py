"""sf-pet-care REST API - the one FastAPI app (Cloud Run service 1).

Run from the repo root:
    fastapi dev api/main.py      # then open http://127.0.0.1:8000/docs

GET reads only. POST changes things (the only POST runs the ETL).
    GET  /neighborhoods   Jonah's polygons from the bucket (GeoJSON)
    GET  /businesses      Maurice's businesses from the bucket
    GET  /census          Jessie's census tracts from the bucket
    GET  /processed       newest processed CSV (Streamlit reads this)
    POST /etl             run_etl(): refresh sources, integrate, save (Cloud Scheduler)

POST /etl?dry_run=true fetches and integrates without writing to the bucket.
Every endpoint only CALLS functions in etl/pipeline.py.
"""

import io

import pandas as pd
from fastapi import FastAPI, HTTPException, Response

from etl import pipeline

app = FastAPI(title="sf-pet-care API")


def _json(df):
    """DataFrame -> JSON response (to_json turns NaN into null)."""
    return Response(df.to_json(orient="records"), media_type="application/json")


@app.get("/neighborhoods")
def neighborhoods(dev_mode: bool = True):
    gdf = pipeline.get_neighborhoods(dev_mode, refresh=False)
    return Response(gdf.to_json(), media_type="application/geo+json")  # map shapes


@app.get("/businesses")
def businesses(dev_mode: bool = True):
    return _json(pipeline.get_businesses(dev_mode, refresh=False))


@app.get("/census")
def census(dev_mode: bool = True):
    df = pipeline.get_census(dev_mode)
    if df is None:
        raise HTTPException(status_code=503, detail="census data (acs.json) not available")
    return _json(df)


@app.post("/etl")
def etl(dev_mode: bool = True, dry_run: bool = False):
    try:
        summary = pipeline.run_etl(dev_mode, dry_run)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ETL failed: {e}")
    summary.pop("_result", None)  # the table itself is not JSON; see /processed
    return summary


@app.get("/processed")
def processed(dev_mode: bool = True):
    """Newest processed CSV from the bucket - reads only, never re-runs."""
    from sources.neighborhood import get_client  # same client every source uses

    client, bucket_name = get_client(dev_mode)
    blobs = list(client.bucket(bucket_name).list_blobs(prefix="processed/"))
    if not blobs:
        raise HTTPException(status_code=404, detail="no processed output yet; call /etl first")

    newest = max(blobs, key=lambda b: b.name)  # names end in a UTC timestamp
    return _json(pd.read_csv(io.BytesIO(newest.download_as_bytes())))
