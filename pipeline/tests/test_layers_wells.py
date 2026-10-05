"""Phase 4 wells (TWDB): lithology parsing, per-log hard-layer feet, radius choice, plant aggregation, loader."""
import io
import re
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from pipeline.common import load_config
from pipeline.phase_4_geo import common as g
from pipeline.phase_4_geo import load as ld
from pipeline.phase_4_geo import wells as wl


@pytest.fixture
def cfg():
    return load_config()


def test_read_lithology_keeps_pipes_in_description():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SDRDownload/WellLithology.txt",
                   "WellReportTrackingNumber|MigratedSortNumber|TopDepth|BottomDepth|LithologyDescription\n"
                   "1|0|0|10|CALICHE | HARD\n1|0|10|abc|bad depth\n2|0|0|5|sand\n")
    li = wl.read_lithology(zipfile.ZipFile(buf))
    assert list(li["id"]) == [1, 2] and li["desc"].iloc[0] == "CALICHE | HARD"


def test_log_metrics_clips_to_bore_depth_and_classifies(cfg):
    c = cfg["layers"]["wells"]
    li = pd.DataFrame({"id": [1, 1, 1, 1, 2, 3], "top_ft": [0, 10, 40, 480, 0, 0], "bottom_ft": [10, 40, 400, 900, 60, 20],
                       "desc": ["topsoil", "Caliche, white", "LIMESTONE", "gypsum and anhydrite", "callichi", "sand"]})
    m = wl.log_metrics(li, c).set_index("id")
    assert m.loc[1, "caliche_ft"] == 30 and m.loc[1, "gypsum_ft"] == 20       # 480-500 only: clipped at bore depth
    assert m.loc[1, "hard_ft"] == 50 and m.loc[1, "rock_ft"] == 360 and m.loc[1, "log_depth_ft"] == 900
    assert m.loc[2, "caliche_ft"] == 60                                     # misspelt caliche still matches
    assert 3 not in m.index                                                 # 20 ft log < min_log_depth_ft


def test_pick_radius():
    d = np.array([0.5, 1, 2, 4, 5, 7, 8, 9])
    assert wl.pick_radius(d, [3, 6, 10], 3) == 3
    assert wl.pick_radius(d, [3, 6, 10], 5) == 6
    assert wl.pick_radius(d, [3, 6, 10], 9) is None


def test_compute_plant_aggregates(cfg):
    plants = g.with_point_fallback(gpd.GeoDataFrame(
        pd.DataFrame({"eia_id": [1, 2], "filter_status": ["pass", "pass"], "lon": [-103.0, -97.0], "lat": [31.0, 31.0]}),
        geometry=[box(-103.005, 30.995, -102.995, 31.005), box(-97.005, 30.995, -96.995, 31.005)], crs="EPSG:4326"))
    # plant 1: six logs ~1 mi away, two of them with 30 ft gypsum; plant 2: nothing nearby
    ids = list(range(10, 16))
    wells = pd.DataFrame({"id": ids, "county": "Pecos", "lat": 31.02, "lon": -103.0, "year": 2015,
                          "use": ["Domestic"] * 5 + ["Closed-Loop Geothermal"]})
    lith = pd.DataFrame({"id": ids + [10, 11], "top_ft": [0] * 6 + [100, 100], "bottom_ft": [200] * 6 + [130, 130],
                         "desc": ["sand"] * 6 + ["GYPSUM", "gyp"]})
    src = {"sdr_wells": wells, "sdr_lithology": lith,
           "sdr_levels": pd.DataFrame({"id": [10, 11, 12], "depth_ft": [150.0, 250.0, 200.0], "year": [2015] * 3}),
           "gwdb_wells": pd.DataFrame({"swn": ["A"], "county": ["Pecos"], "gcd": ["Middle Pecos GCD"], "aquifer": ["Edwards-Trinity"],
                                       "lat": [31.0], "lon": [-103.01]}),
           "gwdb_levels": pd.DataFrame({"swn": ["A"], "year": [2024], "depth_ft": [-5.0]})}   # flowing: left out
    out = wl.compute(plants, src, cfg, "2026-10-05").set_index("eia_id")
    p1 = out.loc[1]
    assert p1["n_logs"] == 6 and p1["logs_radius_mi"] == 3 and p1["thick_hard_layer_share"] == pytest.approx(2 / 6, abs=1e-3)
    assert p1["n_geothermal_bores"] == 1 and p1["depth_to_water_ft"] == 200 and p1["water_level_sources"] == "sdr 3"
    assert p1["gcd_majority"] == "Middle Pecos GCD" and p1["wells_confidence"] == "medium"
    p2 = out.loc[2]
    assert np.isnan(p2["thick_hard_layer_share"]) and np.isnan(p2["depth_to_water_ft"]) and p2["n_logs"] == 0
    assert p2["wells_confidence"] == "low"
    assert set(out.reset_index().columns) | {"run_id"} == set(ld.WELLS_COLUMNS)


def test_loader_columns_match_migration():
    sql = next(Path("supabase/migrations").glob("*_layers_wells.sql")).read_text(encoding="utf-8")
    body = re.search(r"create table public\.layers_wells \((.*?)\n\);", sql, re.S).group(1)
    cols = [m.group(1) for line in body.splitlines() if (m := re.match(r"\s*([a-z_0-9]+)\s", line.split("--")[0]))]
    assert [c for c in cols if c != "loaded_at"] == ld.WELLS_COLUMNS
