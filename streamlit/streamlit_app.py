import os

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")
CATEGORIES = ["veterinary", "pet_care", "pet_store"]

st.set_page_config(page_title="SF Pet Care", layout="wide")


@st.cache_data(ttl=600)
def get(path):
    r = requests.get(f"{API_URL}{path}", timeout=60)
    r.raise_for_status()
    return pd.DataFrame(r.json())


st.title("Pet-care services by San Francisco neighborhood")
st.caption(f"Data from the sf-pet-care API ({API_URL})")

try:
    summary = get("/processed")
    businesses = get("/businesses")
except requests.RequestException as e:
    st.error(f"Could not reach the API: {e}")
    st.stop()

st.sidebar.header("Filters")
hoods = st.sidebar.multiselect("Neighborhoods", sorted(summary["neighborhood"]),
                               help="Leave empty to show all")
cats = st.sidebar.multiselect("Business type", CATEGORIES, default=CATEGORIES)

shown = summary[summary["neighborhood"].isin(hoods)] if hoods else summary
places = businesses.dropna(subset=["neighborhood"])
places = places[places["pet_category"].isin(cats)]
if hoods:
    places = places[places["neighborhood"].isin(hoods)]

c1, c2, c3 = st.columns(3)
c1.metric("Pet-care businesses", int(shown[cats].sum().sum()))
c2.metric("Neighborhoods shown", len(shown))
c3.metric("Neighborhoods with none", int((shown[cats].sum(axis=1) == 0).sum()))

st.subheader("Businesses per neighborhood")
chart = shown.set_index("neighborhood")[cats]
chart = chart.loc[chart.sum(axis=1).sort_values(ascending=False).index]
st.bar_chart(chart)

st.subheader("Where they are")
st.map(places.dropna(subset=["latitude", "longitude"])[["latitude", "longitude"]])

st.subheader("Business list")
st.dataframe(
    places[["dba_name", "pet_category", "neighborhood", "full_business_address"]]
    .sort_values(["neighborhood", "dba_name"]),
    hide_index=True, width="stretch",
)
