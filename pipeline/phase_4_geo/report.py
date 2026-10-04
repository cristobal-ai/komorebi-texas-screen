"""Print a Phase 4 layer next to the plant names, optionally for one county (no shell quoting needed).

    python -m pipeline.phase_4_geo.report --layer transmission --county Pecos
    python -m pipeline.phase_4_geo.report --layer transmission            # all plants, sorted by county
"""
from __future__ import annotations

import argparse

import pandas as pd

from pipeline.common import DATA_DIR
from pipeline.phase_4_geo.common import LAYERS_DIR

SHOW = {
    "transmission": ["grid_voltage_kv", "dist_345kv_sub_mi", "nearest_345kv_sub_name", "dist_345kv_line_mi",
                     "dist_138kv_line_mi", "kv_classes_within_near", "poi_kv_seen", "transmission_confidence"],
}


def table(layer: str, county: str | None = None) -> pd.DataFrame:
    d = pd.read_parquet(LAYERS_DIR / f"{layer}.parquet")
    p = pd.read_parquet(DATA_DIR / "plants.parquet")[["eia_id", "plant_name", "county", "tier", "ac_mw", "grid_voltage_kv"]]
    m = p.merge(d, on="eia_id", how="left")
    if county:
        m = m[m["county"].fillna("").str.lower() == county.lower()]
    cols = ["plant_name", "county", "tier", "ac_mw"] + SHOW.get(layer, [c for c in d.columns if c != "eia_id"])
    return m.sort_values(["county", "ac_mw"], ascending=[True, False])[cols].round(1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", required=True)
    ap.add_argument("--county")
    a = ap.parse_args(argv)
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_colwidth", 28)
    print(table(a.layer, a.county).to_string(index=False))


if __name__ == "__main__":
    main()
