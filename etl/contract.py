"""
Shared data contract for the sf-pet-care unified ETL.

Each source owner imports this and runs it against their DataFrame before
opening a PR. If it passes, the unified ETL can consume your output without
anyone having to read your code.

    from contract import check_businesses
    df = fetch_businesses()
    check_businesses(df)      # raises if the contract is not met

Why this exists: a wrong column NAME crashes loudly and is easy to fix. A wrong
column TYPE, or latitude and longitude the wrong way round, fails silently and
produces a dataset that looks fine and is wrong. This catches both.
"""

import pandas as pd


# --------------------------------------------------------------------------
# What each source must return
# --------------------------------------------------------------------------

BUSINESSES = {
    "business_id":     "object",    # str  — uniqueid from the API
    "dba_name":        "object",    # str  — trading name
    "address":         "object",    # str  — full_business_address
    "latitude":        "float64",   # float — from location.coordinates[1]
    "longitude":       "float64",   # float — from location.coordinates[0]
    "naics_code":      "object",    # str  — self_reported_naics_code
    "lic_description": "object",    # str  — lic_code_description
    "sf_neighborhood": "object",    # str  — neighborhoods_analysis_boundaries
}

CENSUS = {
    "tract_geoid":      "object",   # str — keep as string, leading zeros matter
    "total_population": "int64",    # int — ACS B01003_001E
}

NEIGHBORHOODS = {
    "nhood":    "object",           # str — neighborhood name
    "geometry": "geometry",         # shapely MultiPolygon, CRS EPSG:4326
}


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def _check(df, spec, name):
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"{name}: expected a DataFrame, got {type(df).__name__}")

    missing = [c for c in spec if c not in df.columns]
    if missing:
        raise ValueError(
            f"{name}: missing required columns {missing}\n"
            f"  you returned: {list(df.columns)}"
        )

    if len(df) == 0:
        raise ValueError(f"{name}: returned 0 rows")

    problems = []
    for col, want in spec.items():
        if want == "geometry":
            continue
        got = str(df[col].dtype)
        if want == "float64" and got not in ("float64", "float32"):
            problems.append(f"  {col}: expected float, got {got}")
        elif want == "int64" and got not in ("int64", "int32"):
            problems.append(f"  {col}: expected int, got {got}")
        elif want == "object" and got != "object":
            problems.append(f"  {col}: expected str, got {got}")
    if problems:
        raise TypeError(f"{name}: wrong column types\n" + "\n".join(problems))

    extra = [c for c in df.columns if c not in spec]
    print(f"OK  {name}: {len(df):,} rows, all required columns present")
    if extra:
        print(f"    (extra columns, fine to keep: {extra})")
    return True


def check_businesses(df):
    _check(df, BUSINESSES, "businesses")

    # San Francisco sanity box. Catches swapped lat/lon immediately: SF is
    # about 37.7 N, -122.4 W. If these are reversed nothing will match later
    # and the pipeline will silently produce an empty join.
    lat_ok = df["latitude"].between(37.6, 37.9).mean()
    lon_ok = df["longitude"].between(-123.2, -122.3).mean()
    if lat_ok < 0.9 or lon_ok < 0.9:
        raise ValueError(
            f"businesses: coordinates look wrong for San Francisco\n"
            f"  latitude in range:  {lat_ok:.1%} (want >90%)\n"
            f"  longitude in range: {lon_ok:.1%} (want >90%)\n"
            f"  most likely cause: latitude and longitude are swapped.\n"
            f"  the API returns location.coordinates as [longitude, latitude]."
        )
    print(f"    coordinates look like San Francisco")
    return True


def check_census(df):
    _check(df, CENSUS, "census")
    if (df["total_population"] < 0).any():
        raise ValueError("census: negative population values present")
    print(f"    population total: {df['total_population'].sum():,}")
    return True


def check_neighborhoods(gdf):
    _check(gdf, NEIGHBORHOODS, "neighborhoods")

    crs = getattr(gdf, "crs", None)
    if crs is None:
        raise ValueError("neighborhoods: no CRS set. Expected EPSG:4326.")
    if "4326" not in str(crs):
        raise ValueError(
            f"neighborhoods: CRS is {crs}, expected EPSG:4326.\n"
            f"  fix with: gdf = gdf.to_crs('EPSG:4326')\n"
            f"  a CRS mismatch makes the spatial join return zero matches "
            f"without raising an error."
        )
    if len(gdf) != 41:
        print(f"    note: {len(gdf)} neighborhoods (expected 41) — worth checking")
    print(f"    CRS {crs}, {len(gdf)} polygons")
    return True


if __name__ == "__main__":
    print(__doc__)
    for label, spec in [("businesses", BUSINESSES),
                        ("census", CENSUS),
                        ("neighborhoods", NEIGHBORHOODS)]:
        print(f"\n{label}:")
        for col, typ in spec.items():
            print(f"  {col:<18} {typ}")
