"""Phase 4 gas pipelines + flood layers: ArcGIS paging, distances, transmission vs gathering, flood shares, loaders."""
import json
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, box, mapping

from pipeline.common import load_config
from pipeline.phase_4_geo import arcgis
from pipeline.phase_4_geo import common as g
from pipeline.phase_4_geo import flood as fl
from pipeline.phase_4_geo import gas_pipelines as gp
from pipeline.phase_4_geo import load as ld


@pytest.fixture
def cfg():
    return load_config()


class FakeResp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class FakeSession:
    """Serves features in pages of resultRecordCount, like an ArcGIS layer."""

    def __init__(self, feats):
        self.feats, self.calls = feats, []

    def get(self, url, params=None, timeout=None, headers=None):
        data = params
        self.calls.append(data)
        if data.get("returnCountOnly"):
            return FakeResp({"count": len(self.feats)})
        off, n = int(data["resultOffset"]), int(data["resultRecordCount"])
        return FakeResp({"type": "FeatureCollection", "features": self.feats[off:off + n]})


def feat(i, geom, **props):
    return {"type": "Feature", "geometry": mapping(geom), "properties": {"OBJECTID": i, **props}}


def test_arcgis_query_pages_until_short_page_and_sends_browser_agent():
    feats = [feat(i, LineString([(-103, 31), (-103, 31.01)]), OPERATOR=f"op{i}") for i in range(5)]
    s = FakeSession(feats)
    out = arcgis.query("https://x/MapServer/12", "1=1", ["OBJECTID", "OPERATOR"], (-104, 30, -102, 32), page=2,
                       session=s, sleep=lambda *_: None)
    assert len(out) == 5 and len(s.calls) == 3 and out.crs.to_epsg() == 4326
    assert s.calls[0]["orderByFields"] == "OBJECTID" and s.calls[0]["outSR"] == "4326"
    assert "Mozilla" in arcgis.USER_AGENT


def test_arcgis_query_refuses_to_truncate():
    feats = [feat(i, LineString([(-103, 31), (-103, 31.01)])) for i in range(5)]
    with pytest.raises(RuntimeError, match="more than"):
        arcgis.query("https://x/MapServer/12", "1=1", ["OBJECTID"], (-104, 30, -102, 32), page=2, max_pages=2,
                     session=FakeSession(feats), sleep=lambda *_: None)


def test_arcgis_error_payload_is_retried_then_raised():
    class Err:
        def get(self, *a, **k):
            return FakeResp({"error": {"code": 400, "message": "bad where"}})
    with pytest.raises(RuntimeError, match="bad where"):
        arcgis.query("https://x/MapServer/12", "x", ["OBJECTID"], (0, 0, 1, 1), retries=2, session=Err(), sleep=lambda *_: None)


@pytest.fixture
def plants():
    poly = lambda lon, lat: box(lon - 0.002, lat - 0.002, lon + 0.002, lat + 0.002)
    df = pd.DataFrame({"eia_id": [1, 2, 3], "filter_status": ["pass", "pass", "review"],
                       "lon": [-103.0, -100.0, -97.0], "lat": [31.0, 31.0, 30.8]})
    return g.with_point_fallback(gpd.GeoDataFrame(df, geometry=[poly(-103.0, 31.0), poly(-100.0, 31.0), None], crs="EPSG:4326"))


def test_where_clause_and_boxes(cfg, plants):
    c = cfg["layers"]["gas_pipelines"]
    w = gp.where_clause(c)
    assert w.startswith("STATUS = 'In Service' AND COMMODITY_DESCRIPTION IN (") and "'NATURAL GAS'" in w
    boxes = gp.plant_boxes(plants, c)
    assert len(boxes) == 3 and all(b[2] - b[0] == pytest.approx(2 * c["bbox_half_deg"]) for b in boxes)
    # the search radius fits inside every box up to the Panhandle's north edge (36.5 N)
    worst_lon_mi = (c["bbox_half_deg"] - c["bbox_snap_deg"] / 2) * 69.17 * np.cos(np.radians(36.6))
    assert worst_lon_mi >= c["search_radius_mi"]


def test_gas_compute_separates_transmission_from_gathering(cfg, plants):
    # plant 1 at (-103, 31): gathering line ~0.7 mi east, transmission line ~3.5 mi east (1 deg lon ≈ 59.3 mi at 31 N)
    lines = gpd.GeoDataFrame({
        "OBJECTID": [1, 2, 3],
        "OPERATOR": ["Gatherco", "Big Pipe LP", "Far Away"],
        "COMMODITY_DESCRIPTION": ["NATURAL GAS"] * 3,
        "SYSTEM_NAME": ["G1", "MAINLINE", "X"],
        "SYSTEM_TYPE": ["Natural Gas Gathering               ", "Natural/Other Gas Transmission      ", "Natural/Other Gas Transmission"],
        "DIAMETER": [4.0, 24.0, 0.0],
        "INTERSTATE": ["No", "Yes", "No"],
        "STATUS": ["In Service"] * 3,
    }, geometry=[LineString([(-102.986, 30.9), (-102.986, 31.1)]), LineString([(-102.94, 30.9), (-102.94, 31.1)]),
                 LineString([(-96.0, 30.0), (-96.0, 30.1)])], crs="EPSG:4326")
    out = gp.compute(plants, lines, cfg, "2026-10-05").set_index("eia_id")
    p1 = out.loc[1]
    assert 0.5 < p1["dist_gas_any_mi"] < 0.8
    assert 3.2 < p1["dist_gas_transmission_mi"] < 3.6
    assert p1["gas_transmission_operator"] == "Big Pipe LP" and p1["gas_transmission_diameter_in"] == 24.0
    assert p1["gas_transmission_interstate"] is True
    assert p1["n_gas_transmission_near"] == 1 and p1["max_gas_transmission_diameter_near_in"] == 24.0
    # plant 2 is ~180 mi from everything: beyond the search radius → null, never a big number
    assert np.isnan(out.loc[2, "dist_gas_transmission_mi"]) and out.loc[2, "gas_transmission_operator"] is None
    assert np.isnan(out.loc[2, "dist_gas_any_mi"])
    assert out.loc[3, "gas_confidence"] == "low" and out.loc[1, "gas_confidence"] == "medium"


def test_gas_compute_with_no_lines_gives_nulls(cfg, plants):
    out = gp.compute(plants, arcgis.empty(gp.FIELDS), cfg, "2026-10-05")
    assert out["dist_gas_transmission_mi"].isna().all() and (out["n_gas_transmission_near"] == 0).all()


# ---- flood ------------------------------------------------------------------------------------------------------------
def zones_frame(rows):
    """rows: (geom in EPSG:5070 metres, FLD_ZONE, ZONE_SUBTY, SFHA_TF) → GeoDataFrame in EPSG:4326."""
    gdf = gpd.GeoDataFrame([{"OBJECTID": i, "FLD_ZONE": z, "ZONE_SUBTY": s, "SFHA_TF": t} for i, (_, z, s, t) in enumerate(rows)],
                           geometry=[r[0] for r in rows], crs=g.METRIC_CRS)
    return gdf.to_crs("EPSG:4326")


ARRAY = box(0, 0, 1000, 1000)          # 1 km² ≈ 247 acres, in EPSG:5070 metres (near the CRS origin, -96 / 23 N)


def mapped(fp=ARRAY):
    cov = gpd.GeoSeries([fp.buffer(50)], crs=g.METRIC_CRS).to_crs("EPSG:4326").iloc[0]
    return {"mapped": True, "study_ids": ["48201C"], "covered_wkt": cov.wkt, "fetched": "2026-10-05"}


def test_flood_shares_union_overlaps_and_flag(cfg):
    z = zones_frame([
        (box(0, 0, 200, 1000), "AE", None, "T"),                       # 20% SFHA
        (box(100, 0, 200, 1000), "AE", "FLOODWAY", "T"),               # inside the AE strip: 10% floodway, not double-counted
        (box(200, 0, 300, 1000), "X", "0.2 PCT ANNUAL CHANCE FLOOD HAZARD", "F"),
        (box(900, 0, 1000, 1000), "D", None, "F"),
    ])
    r = fl.analyze(ARRAY, z, mapped(), cfg)
    assert r["flood_status"] == "ok" and r["nfhl_mapped_share"] == pytest.approx(1.0, abs=1e-3)
    assert r["sfha_share"] == pytest.approx(0.20, abs=2e-3)
    assert r["floodway_share"] == pytest.approx(0.10, abs=2e-3)
    assert r["x500_share"] == pytest.approx(0.10, abs=2e-3) and r["zone_d_share"] == pytest.approx(0.10, abs=2e-3)
    assert r["flood_zones"] == "AE;D;X" and r["flood_flag"] is True
    assert r["sfha_acres"] == pytest.approx(0.2 * 1e6 / fl.M2_PER_ACRE, rel=0.01)


def test_flood_mapped_without_zones_is_zero_not_null(cfg):
    r = fl.analyze(ARRAY, fl.arcgis.empty(fl.ZONE_FIELDS), mapped(), cfg)
    assert r["sfha_share"] == 0.0 and r["flood_flag"] is False and r["flood_zones"] is None


def test_flood_not_mapped_is_unknown_not_safe(cfg):
    r = fl.analyze(ARRAY, fl.arcgis.empty(fl.ZONE_FIELDS), {"mapped": False, "study_ids": [], "fetched": "2026-10-05"}, cfg)
    assert r["flood_status"] == "not_mapped" and r["flood_flag"] is None and np.isnan(r["sfha_share"])


def test_flood_partial_coverage_reports_mapped_share(cfg):
    half = gpd.GeoSeries([box(-10, -10, 500, 1010)], crs=g.METRIC_CRS).to_crs("EPSG:4326").iloc[0]
    r = fl.analyze(ARRAY, fl.arcgis.empty(fl.ZONE_FIELDS), {"mapped": True, "study_ids": ["X"], "covered_wkt": half.wkt,
                                                             "fetched": "2026-10-05"}, cfg)
    assert r["nfhl_mapped_share"] == pytest.approx(0.5, abs=0.01)


def test_flood_point_plants_are_buffered(cfg, plants):
    fps = fl.footprints(plants, cfg)
    assert fps.iloc[2].geom_type == "Polygon" and fps.iloc[2].area == pytest.approx(np.pi * 300 ** 2, rel=0.01)


def test_flood_fetch_caches_and_skips_zones_when_unmapped(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(fl, "raw_dir", lambda name: tmp_path)
    s = FakeSession([])                                # availability returns nothing → not mapped → no zone query
    fp = gpd.GeoSeries([ARRAY], crs=g.METRIC_CRS).to_crs("EPSG:4326").iloc[0]
    zones, meta = fl.fetch_plant(7, fp, cfg, session=s, sleep=lambda *_: None)
    assert meta["mapped"] is False and len(zones) == 0 and len(s.calls) == 1
    zones, meta = fl.fetch_plant(7, fp, cfg, session=s, sleep=lambda *_: None)   # cache hit
    assert len(s.calls) == 1 and (tmp_path / "plants" / "7.json").exists()


# ---- loaders ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,columns", [("gas_pipelines", ld.GAS_PIPELINES_COLUMNS), ("flood", ld.FLOOD_COLUMNS)])
def test_loader_columns_match_migration(name, columns):
    sql = next(Path("supabase/migrations").glob(f"*_layers_{name}.sql")).read_text(encoding="utf-8")
    body = re.search(rf"create table public\.layers_{name} \((.*?)\n\);", sql, re.S).group(1)
    cols = [m.group(1) for line in body.splitlines() if (m := re.match(r"\s*([a-z_0-9]+)\s", line.split("--")[0]))]
    assert [c for c in cols if c != "loaded_at"] == columns


def test_flood_rows_serialize(cfg):
    row = {"eia_id": 1} | fl.analyze(ARRAY, fl.arcgis.empty(fl.ZONE_FIELDS), mapped(), cfg)
    rows = ld.to_rows(pd.DataFrame([row]), ld.FLOOD_COLUMNS, "r1")
    json.dumps(rows)
    assert rows[0]["flood_flag"] is False and rows[0]["run_id"] == "r1"
