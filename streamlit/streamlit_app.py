"""Explore pet-care services in each San Francisco neighborhood.

Three views:
  * Neighborhood counts  - pet-care businesses per neighborhood, with dots for each business
  * Census tracts        - one dot per census tract from /census (needs lat/lon columns), sized by population
  * Vet access grid      - SF cut into small squares (default 0.25 sq mi); for every square,
                           the closest vet and how far away it is

Needs: streamlit, pandas, requests, python-dotenv, plotly>=5.24
Reads from the sf-pet-care API:
    /neighborhoods  GeoJSON boundaries (41 polygons)
    /processed      counts per neighborhood by category
    /businesses     individual businesses with lat/lon

Run (with the API up):
    streamlit run streamlit_app.py
"""
import math
import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
API_URL = (os.getenv("API_URL") or os.getenv("API_SERVICE_URL")
           or "http://127.0.0.1:8000").rstrip("/")

CATEGORIES = ["veterinary", "pet_care", "pet_store"]
LABELS = {
    "veterinary": "Veterinary",
    "pet_care": "Grooming / boarding / walking",
    "pet_store": "Pet stores",
}
COLORS = {
    "veterinary": "rgb(214,39,40)",
    "pet_care": "rgb(255,127,14)",
    "pet_store": "rgb(44,160,44)",
}
SF_CENTER = {"lat": 37.76, "lon": -122.44}
SF_BOX = (-123.2, -122.3, 37.6, 37.9)  # lon_min, lon_max, lat_min, lat_max
VIEW_COUNTS, VIEW_GRID, VIEW_TRACTS = "Neighborhood counts", "Vet access grid", "Census tracts"
MODE_DIST, MODE_WHICH = "Distance to closest vet", "Which vet is closest"

st.set_page_config(page_title="SF Pet Care by Neighborhood", layout="wide")


@st.cache_data(ttl=600, show_spinner="Loading data...")
def fetch(path):
    r = requests.get(f"{API_URL}{path}", timeout=60)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------------ geometry
def coords(geom):
    """Yield every (x, y) in a GeoJSON geometry, whatever its nesting."""
    stack = [geom.get("coordinates", [])]
    while stack:
        item = stack.pop()
        if item and isinstance(item[0], (int, float)):
            yield item[0], item[1]
        else:
            stack.extend(item)


def bounds(features):
    pts = [c for f in features if f.get("geometry")
           for c in coords(f["geometry"])]
    if not pts:
        return None
    xs, ys = zip(*pts)
    return min(xs), max(xs), min(ys), max(ys)


def view_for(box):
    """Map centre + zoom that fits a (lon_min, lon_max, lat_min, lat_max) box."""
    lon0, lon1, lat0, lat1 = box
    span = max(lon1 - lon0, (lat1 - lat0) * 1.3, 1e-4)
    zoom = max(1, min(15, math.log2(703 / span) - 0.3))
    return {"lat": (lat0 + lat1) / 2, "lon": (lon0 + lon1) / 2}, zoom


def normalise(geo):
    """Make sure every feature has properties.neighborhood."""
    for f in geo.get("features", []):
        props = f.setdefault("properties", {})
        props["neighborhood"] = props.get("neighborhood") or props.get("nhood")
    return geo


def miles(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * \
        math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3958.8 * math.asin(math.sqrt(h))


def point_in_rings(x, y, rings):
    """Even-odd test over a polygon's rings (outer ring first, then holes)."""
    inside = False
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def prepare_polygons(features):
    out = []
    for f in features:
        g, name = f.get("geometry"), f["properties"].get("neighborhood")
        if not g or not name:
            continue
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for rings in polys:
            xs, ys = [p[0] for p in rings[0]], [p[1] for p in rings[0]]
            out.append((name, rings, min(xs), max(xs), min(ys), max(ys)))
    return out


def find_hood(x, y, polys):
    for name, rings, x0, x1, y0, y1 in polys:
        if x0 <= x <= x1 and y0 <= y <= y1 and point_in_rings(x, y, rings):
            return name
    return None


@st.cache_data(show_spinner="Building grid and finding closest vets...")
def compute_grid(_features, vets, cell_miles):
    """Cut SF into squares of `cell_miles` per side. Keep squares whose centre is on land
    inside a neighborhood, and find the closest vet to each centre."""
    polys = prepare_polygons(_features)
    lon0, lon1, lat0, lat1 = bounds(_features)
    dlat = cell_miles / 69.0
    dlon = cell_miles / (69.17 * math.cos(math.radians((lat0 + lat1) / 2)))
    vet_rows = list(vets[["dba_name", "full_business_address",
                    "latitude", "longitude"]].itertuples(index=False))
    rows, r, lat = [], 0, lat0
    while lat < lat1:
        c, lon = 0, lon0
        while lon < lon1:
            clat, clon = lat + dlat / 2, lon + dlon / 2
            hood = find_hood(clon, clat, polys)
            if hood and vet_rows:
                d, v = min(((miles(clat, clon, v[2], v[3]), v)
                           for v in vet_rows), key=lambda t: t[0])
                rows.append({
                    "cell_id": f"{r}_{c}", "neighborhood": hood,
                    "lon0": lon, "lon1": lon + dlon, "lat0": lat, "lat1": lat + dlat,
                    "miles": round(d, 3), "vet": v[0], "vet_address": v[1],
                    "vet_lat": v[2], "vet_lon": v[3],
                })
            lon += dlon
            c += 1
        lat += dlat
        r += 1
    return pd.DataFrame(rows)


def grid_geojson(cells):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": r.cell_id},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [r.lon0, r.lat0], [r.lon1, r.lat0], [r.lon1, r.lat1], [r.lon0, r.lat1], [r.lon0, r.lat0]]]}}
            for r in cells.itertuples()
        ],
    }


def outline_layer(geo, names, selected):
    """Transparent neighborhood boundaries drawn on top of whatever is underneath."""
    return go.Choroplethmap(
        geojson=geo, featureidkey="properties.neighborhood", locations=names, z=[0] * len(names),
        colorscale=[[0, "white"], [1, "white"]], showscale=False, marker_opacity=0,
        marker_line_color=["black" if n ==
                           selected else "#444" for n in names],
        marker_line_width=[3 if n == selected else 1 for n in names],
        hoverinfo="skip", showlegend=False,
    )


# ---------------------------------------------------------------- load data
try:
    geo = normalise(fetch("/neighborhoods"))
    counts = pd.DataFrame(fetch("/processed"))
    raw = pd.DataFrame(fetch("/businesses"))
except requests.RequestException as e:
    st.error(f"Could not reach the API at {API_URL}: {e}")
    st.stop()

raw["latitude"] = pd.to_numeric(raw["latitude"], errors="coerce")
raw["longitude"] = pd.to_numeric(raw["longitude"], errors="coerce")
businesses = raw.dropna(subset=["neighborhood", "pet_category"])
vets = raw[raw["pet_category"] == "veterinary"].dropna(
    subset=["latitude", "longitude"]).reset_index(drop=True)

features = geo.get("features", [])
box = bounds(features)
in_sf = bool(
    box) and SF_BOX[0] <= box[0] and box[1] <= SF_BOX[1] and SF_BOX[2] <= box[2] and box[3] <= SF_BOX[3]
names = [f["properties"]["neighborhood"] for f in features]

# ------------------------------------------------------------------ sidebar
st.sidebar.header("View")
view = st.sidebar.radio(
    "Map", [VIEW_COUNTS, VIEW_GRID, VIEW_TRACTS], label_visibility="collapsed")

hood_names = sorted(counts["neighborhood"])
if view == VIEW_TRACTS:
    selected = None
else:
    choice = st.sidebar.selectbox(
        "Neighborhood", ["All neighborhoods"] + hood_names)
    selected = None if choice == "All neighborhoods" else choice

if view == VIEW_COUNTS:
    cats = st.sidebar.multiselect(
        "Business type", CATEGORIES, default=CATEGORIES, format_func=LABELS.get)
    if not cats:
        st.sidebar.warning("Pick at least one business type.")
        st.stop()
elif view == VIEW_GRID:
    cell = st.sidebar.select_slider(
        "Grid square size", options=[0.25, 0.5, 0.75, 1.0], value=0.5,
        format_func=lambda m: f"{m} mi per side ({m * m:.2f} sq mi)",
    )
    mode = st.sidebar.radio("Colour squares by", [MODE_DIST, MODE_WHICH])

# --------------------------------------------------------------------- page
st.title("Pet care in San Francisco neighborhoods")

if not features:
    st.warning("The API returned no neighborhood boundaries.")
elif not in_sf:
    st.warning(
        "Neighborhood boundaries fall outside San Francisco "
        f"(lon {box[0]:.4f} to {box[1]:.4f}, lat {box[2]:.4f} to {box[3]:.4f}). "
        "The geometry in the API/bucket is probably in the wrong coordinate system."
    )
with st.expander("Data check"):
    st.write(f"{len(features)} boundaries, {len(vets)} vets with coordinates")
    st.write("Bounding box (lon_min, lon_max, lat_min, lat_max):", box)


def finish_map(fig, center, zoom):
    fig.update_layout(
        map={"style": "carto-positron", "center": center, "zoom": zoom},
        margin={"l": 0, "r": 0, "t": 0, "b": 0}, height=620,
        legend={"orientation": "h", "y": 1.02, "x": 0},
    )
    st.plotly_chart(fig, width="stretch")


def hood_view(selected):
    if selected:
        sel_box = bounds(
            [f for f in features if f["properties"]["neighborhood"] == selected])
        if sel_box:
            return view_for(sel_box)
    return view_for(box) if in_sf else (SF_CENTER, 11)


# ============================================================ COUNTS VIEW
if view == VIEW_COUNTS:
    st.caption(
        "Active registered businesses classified by NAICS code. Darker neighborhoods have more.")
    counts = counts.copy()
    counts["total"] = counts[cats].sum(axis=1)
    places = businesses[businesses["pet_category"].isin(cats)]

    if selected:
        row = counts[counts["neighborhood"] == selected].iloc[0]
        st.subheader(selected)
        cols = st.columns(len(cats) + 1)
        cols[0].metric("Total", int(row["total"]))
        for col, c in zip(cols[1:], cats):
            col.metric(LABELS[c], int(row[c]))
        rank = int((counts["total"] > row["total"]).sum()) + 1
        st.caption(
            f"Ranks #{rank} of {len(counts)} neighborhoods for the selected types.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Pet-care businesses", int(counts["total"].sum()))
        c2.metric("Neighborhoods", len(counts))
        c3.metric("Neighborhoods with none", int((counts["total"] == 0).sum()))

    cnames = counts["neighborhood"].tolist()
    is_sel = [n == selected for n in cnames]
    hover = "<b>%{location}</b><br>Total: %{z}<br>" + "<br>".join(
        f"{LABELS[c]}: %{{customdata[{i}]}}" for i, c in enumerate(cats)
    ) + "<extra></extra>"
    fig = go.Figure(go.Choroplethmap(
        geojson=geo, featureidkey="properties.neighborhood", locations=cnames, z=counts["total"],
        zmin=0, colorscale="Blues", marker_opacity=0.65,
        marker_line_color=["black" if x else "#555" for x in is_sel],
        marker_line_width=[3 if x else 0.8 for x in is_sel],
        customdata=counts[cats].to_numpy(), hovertemplate=hover, colorbar={"title": "Businesses"},
    ))
    dots = places if not selected else places[places["neighborhood"] == selected]
    dots = dots.dropna(subset=["latitude", "longitude"])
    for c in cats:
        d = dots[dots["pet_category"] == c]
        if d.empty:
            continue
        fig.add_trace(go.Scattermap(
            lat=d["latitude"], lon=d["longitude"], mode="markers", name=LABELS[c],
            marker={"size": 8, "color": COLORS[c]},
            text=d["dba_name"] + "<br>" +
            d["full_business_address"].fillna(""),
            hovertemplate="%{text}<extra></extra>",
        ))
    finish_map(fig, *hood_view(selected))

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Businesses per neighborhood")
        chart = counts.set_index("neighborhood")[cats].rename(columns=LABELS)
        chart = chart.loc[counts.set_index("neighborhood")[
            "total"].sort_values(ascending=False).index]
        st.bar_chart(chart, horizontal=True, height=max(400, 22 * len(chart)))
    with right:
        st.subheader("Business list")
        shown = places if not selected else places[places["neighborhood"] == selected]
        shown = shown.assign(type=shown["pet_category"].map(LABELS))
        st.dataframe(
            shown[["dba_name", "type", "neighborhood", "full_business_address"]]
            .sort_values(["neighborhood", "dba_name"])
            .rename(columns={"dba_name": "name", "full_business_address": "address"}),
            hide_index=True, width="stretch", height=max(400, 22 * min(len(chart), 41)),
        )

# ============================================================== GRID VIEW
elif view == VIEW_GRID:
    st.caption(
        f"San Francisco is cut into squares of {cell * cell:.2f} sq mi ({cell} mi per side). "
        "A square is kept if its center is inside a neighborhood. Distances are straight-line "
        "miles from each square's center to the closest vet."
    )
    if vets.empty or not features:
        st.info(
            "Need both neighborhood boundaries and vets with coordinates to build the grid.")
        st.stop()

    cells = compute_grid(features, vets, cell)
    if cells.empty:
        st.info("No grid squares fell inside the neighborhood boundaries.")
        st.stop()
    shown = cells if not selected else cells[cells["neighborhood"] == selected]
    if shown.empty:
        st.info(
            f"No grid squares have their center in {selected}. Try a smaller square size.")
        st.stop()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Grid squares", len(shown))
    m2.metric("Average distance to a vet", f"{shown['miles'].mean():.2f} mi")
    m3.metric("Farthest square", f"{shown['miles'].max():.2f} mi")
    m4.metric("Squares within 1 mile", f"{(shown['miles'] <= 1).mean():.0%}")

    gj = grid_geojson(shown)
    custom = shown[["neighborhood", "vet", "miles"]].to_numpy()
    hover = ("<b>%{customdata[0]}</b><br>Closest vet: %{customdata[1]}"
             "<br>%{customdata[2]:.2f} mi away<extra></extra>")
    if mode == MODE_DIST:
        layer = go.Choroplethmap(
            geojson=gj, featureidkey="properties.id", locations=shown["cell_id"], z=shown["miles"],
            zmin=0, colorscale="YlOrRd", marker_opacity=0.7, marker_line_width=0.3, marker_line_color="white",
            customdata=custom, hovertemplate=hover, colorbar={
                "title": "Miles to<br>closest vet"},
        )
    else:
        keys = shown["vet"] + "|" + \
            shown["vet_lat"].astype(str) + "|" + shown["vet_lon"].astype(str)
        codes, uniq = pd.factorize(keys)
        n = len(uniq)
        palette = px.colors.qualitative.Dark24 + px.colors.qualitative.Light24
        scale = []
        for i in range(n):
            col = palette[i % len(palette)]
            scale += [[i / n, col], [(i + 1) / n, col]]
        layer = go.Choroplethmap(
            geojson=gj, featureidkey="properties.id", locations=shown["cell_id"], z=codes + 0.5,
            zmin=0, zmax=n, colorscale=scale, showscale=False, marker_opacity=0.6,
            marker_line_width=0.3, marker_line_color="white", customdata=custom, hovertemplate=hover,
        )
    fig = go.Figure(layer)
    fig.add_trace(outline_layer(geo, names, selected))

    used = shown.drop_duplicates(["vet", "vet_lat", "vet_lon"])
    fig.add_trace(go.Scattermap(
        lat=used["vet_lat"], lon=used["vet_lon"], mode="markers", name="Vets (closest to a square shown)",
        marker={"size": 10, "color": "rgb(214,39,40)"},
        text=used["vet"] + "<br>" + used["vet_address"].fillna(""), hovertemplate="%{text}<extra></extra>",
    ))
    finish_map(fig, *hood_view(selected))

    st.subheader("Distance to closest vet, by neighborhood")
    summary = (
        shown.groupby("neighborhood")
        .agg(squares=("cell_id", "count"), avg_miles=("miles", "mean"), farthest_miles=("miles", "max"))
        .round(2).reset_index().sort_values("avg_miles", ascending=False)
        .rename(columns={"avg_miles": "avg distance (mi)", "farthest_miles": "farthest square (mi)"})
    )
    st.dataframe(summary, hide_index=True, width="stretch")
    st.caption(
        "Neighborhoods too small to contain a square center don't appear. Use a smaller square size to include them.")

# ============================================================ TRACTS VIEW
else:
    st.caption(
        "One dot per census tract, at the tract's location. Dot size is population.")
    try:
        tracts = pd.DataFrame(fetch("/census"))
    except requests.RequestException as e:
        st.error(f"Could not load /census: {e}")
        st.stop()

    lat_col = next((c for c in ("lat", "latitude")
                   if c in tracts.columns), None)
    lon_col = next((c for c in ("lon", "longitude")
                   if c in tracts.columns), None)
    if not lat_col or not lon_col:
        st.error(
            f"/census has no lat/lon columns. Columns returned: {list(tracts.columns)}")
        st.stop()

    tracts["lat"] = pd.to_numeric(tracts[lat_col], errors="coerce")
    tracts["lon"] = pd.to_numeric(tracts[lon_col], errors="coerce")
    tracts["population"] = pd.to_numeric(
        tracts["population"], errors="coerce").fillna(0)
    tracts["income"] = pd.to_numeric(tracts["income"], errors="coerce")
    tracts.loc[tracts["income"] < 0, "income"] = float(
        "nan")  # census uses -666666666 for "no data"
    tracts["tract"] = tracts["geoid"].astype(
        str) if "geoid" in tracts.columns else tracts.index.astype(str)
    # drop tracts with no location and the Farallon Islands tract, which is far outside the city
    tracts = tracts.dropna(subset=["lat", "lon"])
    # the neighborhood boundaries' extent (a little padding below)
    lim = box if box else SF_BOX
    tracts = tracts[tracts["lon"].between(
        lim[0] - 0.02, lim[1] + 0.02) & tracts["lat"].between(lim[2] - 0.02, lim[3] + 0.02)]
    if tracts.empty:
        st.info("No census tracts with a location inside San Francisco.")
        st.stop()

    by = st.sidebar.radio(
        "Colour dots by", ["Median household income", "Population"])
    tracts = tracts.assign(
        size=6 + 16 * (tracts["population"] /
                       max(tracts["population"].max(), 1)) ** 0.5,
        text=("Tract " + tracts["tract"] + "<br>Population: " + tracts["population"].map("{:,.0f}".format)
              + "<br>Median income: " + tracts["income"].map(lambda v: "n/a" if pd.isna(v) else f"${v:,.0f}")),
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Census tracts", len(tracts))
    c2.metric("Residents", f"{tracts['population'].sum():,.0f}")
    c3.metric("Median of tract incomes", f"${tracts['income'].median():,.0f}")

    fig = go.Figure()
    if by == "Median household income":
        nodata = tracts[tracts["income"].isna()]
        if not nodata.empty:
            fig.add_trace(go.Scattermap(lat=nodata["lat"], lon=nodata["lon"], mode="markers", name="No income data",
                                        marker={
                                            "size": nodata["size"], "color": "#999"},
                                        text=nodata["text"], hovertemplate="%{text}<extra></extra>"))
        d, color, title = tracts.dropna(
            subset=["income"]), "income", "Median<br>income ($)"
    else:
        d, color, title = tracts, "population", "Population"
    fig.add_trace(go.Scattermap(
        lat=d["lat"], lon=d["lon"], mode="markers", name="Census tracts",
        marker={"size": d["size"], "color": d[color], "colorscale": "Viridis", "showscale": True,
                "colorbar": {"title": title}, "opacity": 0.8},
        text=d["text"], hovertemplate="%{text}<extra></extra>",
    ))
    finish_map(fig, *hood_view(None))

    # ---- income vs. distance to the closest vet, one dot per tract
    st.subheader("Income vs. distance to the closest vet")
    if vets.empty:
        st.info("No vets with coordinates, so distance can't be calculated.")
    else:
        near = [
            min(((miles(t.lat, t.lon, v.latitude, v.longitude), v.dba_name)
                for v in vets.itertuples()), key=lambda x: x[0])
            for t in tracts.itertuples()
        ]
        tracts = tracts.assign(vet_miles=[n[0] for n in near], vet_name=[
                               n[1] for n in near])
        # leaves out tracts with no income data
        d = tracts[tracts["income"] > 0]
        if len(d) < 3:
            st.info("Not enough tracts with income data to plot.")
        else:
            fig2 = go.Figure(go.Scatter(
                x=d["income"], y=d["vet_miles"], mode="markers", marker={"size": 8, "opacity": 0.7},
                text=("Tract " + d["tract"] + "<br>Income: " + d["income"].map("${:,.0f}".format)
                      + "<br>Closest vet: " + d["vet_name"] + " (" + d["vet_miles"].round(2).astype(str) + " mi)"),
                hovertemplate="%{text}<extra></extra>",
            ))
            slope = d["income"].cov(d["vet_miles"]) / d["income"].var()
            intercept = d["vet_miles"].mean() - slope * d["income"].mean()
            xs = [d["income"].min(), d["income"].max()]
            fig2.add_trace(go.Scatter(x=xs, y=[intercept + slope * x for x in xs], mode="lines",
                                      line={"dash": "dash", "color": "#d62728"}, hoverinfo="skip"))
            fig2.update_layout(
                xaxis_title="Median household income ($)", yaxis_title="Miles to closest vet (straight line)",
                showlegend=False, height=450, margin={"l": 0, "r": 0, "t": 10, "b": 0},
            )
            st.plotly_chart(fig2, width="stretch")
            r = d["income"].corr(d["vet_miles"])
            st.caption(
                f"{len(d)} tracts with income data. Correlation r = {r:.2f}. A positive r means higher-income tracts "
                "tend to be farther from a vet, a negative r means closer, and near 0 means no clear pattern. "
                "This shows an association only, and distances are measured from each tract's location point."
            )

    cols = ["tract", "population", "income"] + \
        (["vet_miles"] if "vet_miles" in tracts.columns else [])
   # st.dataframe(
    #  tracts[cols].sort_values("population", ascending=False)
    #  .rename(columns={"income": "median household income", "vet_miles": "miles to closest vet"}),
    # hide_index=True, width="stretch",
   # )
