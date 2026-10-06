"""Phase 4 fiber proxy: interstate refs, corridor distances, lateral = nearer corridor, carrier-hotel latency, loader."""
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, box

from pipeline.common import load_config
from pipeline.phase_4_geo import arcgis
from pipeline.phase_4_geo import common as g
from pipeline.phase_4_geo import fiber as fb
from pipeline.phase_4_geo import load as ld


@pytest.fixture
def cfg():
    return load_config()


def test_interstate_ref(cfg):
    pat = cfg["layers"]["fiber"]["interstate_ref_regex"]
    assert fb.interstate_ref("I 10", pat) == "I-10"
    assert fb.interstate_ref("US 290;I 10", pat) == "I-10"
    assert fb.interstate_ref("I-35E", pat) == "I-35"
    assert fb.interstate_ref("US 287", pat) is None and fb.interstate_ref("SH 130", pat) is None
    assert fb.interstate_ref(None, pat) is None


def test_rail_where(cfg):
    w = fb.rail_where(cfg["layers"]["fiber"])
    assert "RROWNER1 IN ('UP','BNSF','CPKC')" in w and "NET IN ('M')" in w


def test_great_circle_dallas_houston():
    assert fb.great_circle_mi(32.7767, -96.7970, 29.7604, -95.3698) == pytest.approx(225, abs=5)


@pytest.fixture
def plants():
    poly = lambda lon, lat: box(lon - 0.002, lat - 0.002, lon + 0.002, lat + 0.002)
    df = pd.DataFrame({"eia_id": [1, 2], "filter_status": ["pass", "pass"], "lon": [-103.0, -100.0], "lat": [31.0, 31.0]})
    return g.with_point_fallback(gpd.GeoDataFrame(df, geometry=[poly(-103.0, 31.0), poly(-100.0, 31.0)], crs="EPSG:4326"))


def test_compute_lateral_is_nearer_corridor_and_far_is_null(cfg, plants):
    # 1 deg lon ≈ 59.3 mi at 31 N. Plant 1: interstate ~1.1 mi east, rail ~5.9 mi east. Plant 2: nothing within 60 mi.
    rail = gpd.GeoDataFrame({"OBJECTID": [1], "RROWNER1": ["UP"], "SUBDIV": ["Toyah"], "NET": ["M"], "STATEAB": ["TX"]},
                            geometry=[LineString([(-102.9, 30.5), (-102.9, 31.5)])], crs="EPSG:4326")
    hwy = gpd.GeoDataFrame({"osm_id": ["w1"], "ref": ["I-10"]}, geometry=[LineString([(-102.98, 30.5), (-102.98, 31.5)])], crs="EPSG:4326")
    out = fb.compute(plants, rail, hwy, cfg, "2026-10-05").set_index("eia_id")
    p1 = out.loc[1]
    assert 0.9 < p1["dist_interstate_mi"] < 1.3 and 5.5 < p1["dist_class1_rail_mi"] < 6.2
    assert p1["fiber_lateral_miles"] == p1["dist_interstate_mi"] and p1["fiber_corridor"] == "interstate"
    assert p1["nearest_rail_owner"] == "UP" and p1["nearest_interstate"] == "I-10"
    p2 = out.loc[2]
    assert np.isnan(p2["dist_class1_rail_mi"]) and np.isnan(p2["fiber_lateral_miles"]) and p2["fiber_corridor"] is None
    assert p2["nearest_rail_owner"] is None
    assert (out["fiber_confidence"] == "low").all()


def test_carrier_hotel_latency(cfg, plants):
    c = cfg["layers"]["fiber"]
    out = fb.carrier_hotels(plants, c).set_index("eia_id")
    # (31, -103) is nearest San Antonio (~300 mi); RTT = gc × 1.5 × 0.0158
    assert out.loc[1, "nearest_carrier_hotel"].startswith("San Antonio")
    gc = out.loc[1, "carrier_hotel_gc_mi"]
    assert out.loc[1, "latency_rtt_ms_est"] == pytest.approx(gc * c["route_factor"] * c["rtt_ms_per_route_mile"])
    assert out.loc[1, "carrier_hotel_rtt_ms"].count("ms") == len(c["carrier_hotels"])


def test_compute_with_no_sources(cfg, plants):
    out = fb.compute(plants, arcgis.empty(fb.RAIL_FIELDS), arcgis.empty(["osm_id", "ref"]), cfg, "2026-10-05")
    assert out["fiber_lateral_miles"].isna().all() and out["latency_rtt_ms_est"].notna().all()


def test_loader_columns_match_migration():
    sql = next(Path("supabase/migrations").glob("*_layers_fiber.sql")).read_text(encoding="utf-8")
    body = re.search(r"create table public\.layers_fiber \((.*?)\n\);", sql, re.S).group(1)
    cols = [m.group(1) for line in body.splitlines() if (m := re.match(r"\s*([a-z_0-9]+)\s", line.split("--")[0]))]
    assert [c for c in cols if c != "loaded_at"] == ld.FIBER_COLUMNS
