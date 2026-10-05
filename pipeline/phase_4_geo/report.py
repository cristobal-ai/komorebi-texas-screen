"""Print a Phase 4 layer next to the plant names, optionally for one county (no shell quoting needed).

    python -m pipeline.phase_4_geo.report --layer transmission --county Pecos
    python -m pipeline.phase_4_geo.report --layer transmission            # all plants, sorted by county
    python -m pipeline.phase_4_geo.report --layer transmission --poi-mismatch   # EIA POI voltage not seen on a nearby line
"""
from __future__ import annotations

import argparse

import pandas as pd

from pipeline.common import DATA_DIR
from pipeline.phase_4_geo.common import LAYERS_DIR

SHOW = {
    "parcels": ["parcel_status", "n_host_parcels", "parcel_acres_host", "acres_per_mw_parcel", "host_cover_share",
                "headroom_pct_unified", "unified_land_control", "host_owners", "parcel_vintage", "parcels_confidence"],
    "climate": ["hours_below_25c_drybulb", "hours_below_25c_drybulb_min", "hours_below_25c_drybulb_tmy",
                "hours_below_20c_wetbulb", "hours_above_35c_drybulb", "mean_annual_temp_c", "design_drybulb_0p4_c",
                "design_wetbulb_0p4_c", "nsrdb_elevation_m", "climate_confidence"],
    "transmission": ["grid_voltage_kv", "dist_345kv_sub_mi", "nearest_345kv_sub_name", "dist_345kv_line_mi",
                     "dist_138kv_line_mi", "kv_classes_within_near", "poi_kv_seen", "transmission_confidence"],
}


def table(layer: str, county: str | None = None) -> pd.DataFrame:
    d = pd.read_parquet(LAYERS_DIR / f"{layer}.parquet")
    p = pd.read_parquet(DATA_DIR / "plants.parquet")[["eia_id", "plant_name", "county", "tier", "ac_mw", "grid_voltage_kv"]]
    m = p.merge(d, on="eia_id", how="inner")           # plants.parquet also holds the failed plants; the layer covers pass + review
    if county:
        m = m[m["county"].fillna("").str.lower() == county.lower()]
    cols = ["plant_name", "county", "tier", "ac_mw"] + SHOW.get(layer, [c for c in d.columns if c != "eia_id"])
    return m.sort_values(["county", "ac_mw"], ascending=[True, False])[cols].round(1)


def poi_mismatch(layer: str = "transmission") -> pd.DataFrame:
    """Plants whose EIA POI voltage is not on any >= 100 kV line within near_miles (OSM gap, a POI at a distant substation,
    or an EIA voltage error). Sorted by how far the nearest >= 138 kV line is."""
    m = table(layer)
    m = m[m["poi_kv_seen"] == False]                   # noqa: E712  (also excludes null)
    return m.sort_values("dist_138kv_line_mi", ascending=False)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", required=True)
    ap.add_argument("--county")
    ap.add_argument("--poi-mismatch", action="store_true")
    a = ap.parse_args(argv)
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_colwidth", 28)
    out = poi_mismatch(a.layer) if a.poi_mismatch else table(a.layer, a.county)
    print(out.to_string(index=False))
    print(f"\n{len(out)} plants")


if __name__ == "__main__":
    main()
