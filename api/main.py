"""sf-pet-care REST API - the one FastAPI app (Cloud Run service 1).

Run from the repo root:
    fastapi dev api/main.py      # then open http://127.0.0.1:8000/docs

Endpoints:
    GET /run-etl         run the full pipeline (Cloud Scheduler calls this)
    GET /raw/{source}    one raw file from the bucket: businesses | neighborhoods | acs
    GET /processed       the latest processed result (Streamlit reads this)

Every endpoint only CALLS existing functions in etl/ and sources/.
No data logic lives here.
"""

import importlib
import io

import pandas as pd
from fastapi import FastAPI, HTTPException, Response

from etl import pipeline

app = FastAPI(title="sf-pet-care API")

# source name in the URL -> (module in sources/, its download function)
RAW_SOURCES = {
    "businesses": ("registered_businesses", "download_registered_businesses"),
    "neighborhoods": ("neighborhood", "download_neighborhood"),
    "acs": ("acs_data", "download_acs"),
}


def _json(df):
    """DataFrame -> JSON response. to_json turns NaN into null, which plain
    json.dumps refuses to do."""
    return Response(df.to_json(orient="records"), media_type="application/json")


@app.get("/run-etl")
def run_etl(dev_mode: bool = True, dry_run: bool = False):
    """Acquire -> load -> validate -> integrate -> save to the bucket.

    dry_run=true fetches and integrates but writes nothing to the bucket.
    """
    try:
        summary = pipeline.run(dev_mode=dev_mode, dry_run=dry_run)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ETL failed: {e}")
    summary.pop("_result", None)  # the DataFrame itself is not JSON
    return summary


@app.get("/raw/{source}")
def raw(source: str, dev_mode: bool = True):
    """Return one source's raw data exactly as stored in the bucket."""
    if source not in RAW_SOURCES:
        raise HTTPException(status_code=404,
                            detail=f"unknown source; use one of {list(RAW_SOURCES)}")
    module_name, func_name = RAW_SOURCES[source]
    try:
        module = importlib.import_module(f"sources.{module_name}")
    except ImportError:
        raise HTTPException(status_code=503, detail=f"source '{source}' is not available yet")

    df = getattr(module, func_name)(dev_mode=dev_mode)

    if source == "neighborhoods":  # map shapes: return GeoJSON, as Jonah's API did
        return Response(df.to_json(), media_type="application/geo+json")
    return _json(df)


@app.get("/processed")
def processed(dev_mode: bool = True):
    """Return the newest processed CSV from the bucket as JSON rows."""
    from sources.neighborhood import get_client  # same client every source uses

    client, bucket_name = get_client(dev_mode)
    blobs = list(client.bucket(bucket_name).list_blobs(prefix="processed/"))
    if not blobs:
        raise HTTPException(status_code=404, detail="no processed output yet; call /run-etl first")

    newest = max(blobs, key=lambda b: b.name)  # names end in a UTC timestamp
    df = pd.read_csv(io.BytesIO(newest.download_as_bytes()))
    return _json(df)
