"""Shared data contract for the sf-pet-care unified ETL.

Run the relevant check against your DataFrame before opening a PR. If it passes,
the unified ETL can consume your output without anyone reading your code.

    from contract import check_businesses
    df = download_registered_businesses(dev_mode=True)
    check_businesses(df)      # raises if the contract is not met

Column names below are the ones each collector ACTUALLY produces, verified by
running their code live on 2026-10-04. Nobody is asked to rename anything.

Why bother: a wrong column NAME crashes loudly and is easy to fix. A wrong
column TYPE, or latitude and longitude the wrong way round, fails silently and
produces a dataset that looks fine and is wrong. This catches both.
"""

import pandas as pd

# Accepted spellings for a text column. pandas 3 reports "str";
# pandas 2 reports "object". Both are fine.
TEXT = {"str", "object", "string"}
REAL = {"float64", "float32"}
WHOLE = {"int64", "int32"}


# --------------------------------------------------------------------------
# What each source returns
# --------------------------------------------------------------------------

# Maurice - registered_businesses.py -> download_registered_businesses(dev_mode)
BUSINESSES = {
    "uniqueid": TEXT,                  # stable business id
    "dba_name": TEXT,                  # trading name
    "full_business_address": TEXT,     # street address
    "neighborhood": TEXT,              # join key - same name as Jonah's
    "self_reported_naics_code": TEXT,  # 54194 / 81291 / 45391
    "pet_category": TEXT,              # veterinary / pet_care / pet_store
    "longitude": REAL,                 # flattened from the GeoJSON Point
    "latitude": REAL,
}

# Jessie - acs_data.py
CENSUS = {
    "geoid": TEXT,        # 11-char tract GEOID, leading zeros matter
    "population": WHOLE,  # ACS B01003_001E
}

# Jonah - neighborhood.py -> download_neighborhood(dev_mode)
NEIGHBOURHOODS = {
    "neighborhood": TEXT,
    "geometry": {"geometry"},
}
NEIGHBORHOODS = NEIGHBOURHOODS  # alias, both spellings work


# --------------------------------------------------------------------------
# Generic check
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

    problems = [
        f"  {col}: expected one of {sorted(allowed)}, got {df[col].dtype}"
        for col, allowed in spec.items()
        if str(df[col].dtype) not in allowed
    ]
    if problems:
        raise TypeError(f"{name}: wrong column types\n" + "\n".join(problems))

    extra = len(df.columns) - len(spec)
    print(f"OK  {name}: {len(df):,} rows, all required columns present"
          + (f" (+{extra} extra, fine to keep)" if extra > 0 else ""))
    return True


# --------------------------------------------------------------------------
# Per-source checks
# --------------------------------------------------------------------------

def check_businesses(df):
    """Maurice's businesses: coordinates sane, join key populated."""
    _check(df, BUSINESSES, "businesses")

    lon, lat = df["longitude"], df["latitude"]
    usable = lon.notna() & lat.notna()
    if usable.sum() == 0:
        raise ValueError("businesses: every longitude/latitude is empty")

    # San Francisco sits near 37.7 N, -122.4 W. If these two are swapped the
    # numbers still look like numbers, every later join quietly returns nothing,
    # and no error is raised anywhere. Hence an explicit range check.
    lat_ok = lat[usable].between(37.6, 37.9).mean()
    lon_ok = lon[usable].between(-123.2, -122.3).mean()
    if lat_ok < 0.9 or lon_ok < 0.9:
        raise ValueError(
            "businesses: coordinates are not San Francisco\n"
            f"  latitude in range:  {lat_ok:.1%} (want >90%)\n"
            f"  longitude in range: {lon_ok:.1%} (want >90%)\n"
            "  most likely cause: longitude and latitude are swapped."
        )
    print(f"    coordinates look like San Francisco ({usable.sum()}/{len(df)} rows)")

    blank = df["neighborhood"].isna().sum()
    if blank:
        print(f"    note: {blank} row(s) have no neighborhood and will not be counted")
    return True


def check_census(df):
    """Jessie's ACS: no missing-data sentinels, geoid still a string."""
    _check(df, CENSUS, "census")

    # ACS uses large negative sentinels (e.g. -666666666) for "no data".
    if (df["population"] < 0).any():
        raise ValueError("census: negative population present "
                         "(ACS uses -666666666 for missing)")

    bad = df["geoid"].astype(str).str.len() != 11
    if bad.any():
        raise ValueError(
            f"census: {bad.sum()} geoid(s) are not 11 characters, "
            f"e.g. {df.loc[bad, 'geoid'].iloc[0]!r}\n"
            "  usually means geoid was read as an int and lost its leading zero."
        )
    print(f"    {len(df)} tracts, population {df['population'].sum():,}")
    return True


def check_neighborhoods(gdf):
    """Jonah's boundaries: right CRS, expected polygon count."""
    _check(gdf, NEIGHBOURHOODS, "neighborhoods")

    crs = getattr(gdf, "crs", None)
    if crs is not None and "4326" not in str(crs):
        raise ValueError(f"neighborhoods: CRS is {crs}, expected EPSG:4326.\n"
                         "  fix with: gdf = gdf.to_crs('EPSG:4326')")
    if len(gdf) != 41:
        print(f"    note: {len(gdf)} polygons (expected 41)")
    print(f"    CRS {crs or 'not set'}, {len(gdf)} polygons")
    return True


def check_join(businesses, neighborhoods):
    """The join the whole pipeline rests on. Assert it, don't assume it."""
    b = set(businesses["neighborhood"].dropna())
    n = set(neighborhoods["neighborhood"].dropna())
    orphans = b - n
    if orphans:
        raise ValueError(
            f"join: {len(orphans)} business neighborhood(s) match no boundary: "
            f"{sorted(orphans)[:5]}"
        )
    print(f"OK  join: all {len(b)} business neighborhoods match a boundary "
          f"({len(n) - len(b)} boundaries have no pet businesses)")
    return True


if __name__ == "__main__":
    print(__doc__)
    for label, spec in [("businesses", BUSINESSES),
                        ("census", CENSUS),
                        ("neighborhoods", NEIGHBOURHOODS)]:
        print(f"\n{label}:")
        for col, allowed in spec.items():
            print(f"  {col:<28} {'/'.join(sorted(allowed))}")
