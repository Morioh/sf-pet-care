from fastapi import FastAPI, Response

from etl import pipeline

app = FastAPI(title="sf-pet-care API")


def as_json(df):
    return Response(df.to_json(orient="records"), media_type="application/json")


@app.get("/neighborhoods")
def neighborhoods(dev_mode: bool = True):
    return Response(pipeline.get_neighborhoods(dev_mode).to_json(),
                    media_type="application/geo+json")


@app.get("/businesses")
def businesses(dev_mode: bool = True):
    return as_json(pipeline.get_businesses(dev_mode))


@app.get("/census")
def census(dev_mode: bool = True):
    return as_json(pipeline.get_census(dev_mode))


@app.get("/processed")
def processed(dev_mode: bool = True):
    return as_json(pipeline.get_result(dev_mode))


@app.post("/etl")
def etl(dev_mode: bool = True):
    return pipeline.run_etl(dev_mode)
