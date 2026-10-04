"""Phase 4 parcels layer: owner matching, host parcels, headroom, same-owner expansion, status codes, file reading."""
import json
import re
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from pipeline.phase_4_geo import load as ld
from pipeline.phase_4_geo import parcels as pc

M2 = pc.M2_PER_ACRE


def frame(rows):
    """rows: (minx, miny, maxx, maxy, owner, land_use, mkt_value) in metres → parcel frame in EPSG:5070."""
    return gpd.GeoDataFrame(
        {"owner": [r[4] for r in rows], "prop_id": range(len(rows)), "land_use": [r[5] for r in rows],
         "market_value": [r[6] for r in rows], "land_value": [r[6] for r in rows], "county": "Pecos", "year": 2025,
         "source_file": "pecos.zip"},
        geometry=[box(*r[:4]) for r in rows], crs="EPSG:5070")


ARRAY = box(0, 0, 1000, 1000)             # 1,000 m square = 247.1 acres
ARRAY_ACRES = 1_000_000 / M2


def test_owner_normalisation_ignores_punctuation_and_company_words(cfg):
    stop = set(cfg["layers"]["parcels"]["owner_stopwords"])
    assert pc.norm_owner("ACME SOLAR, LLC", stop) == pc.norm_owner("Acme Solar L.L.C.", stop) == "acme solar"
    assert pc.norm_owner("ACME SOLAR 2 LLC", stop) != pc.norm_owner("ACME SOLAR LLC", stop)    # sponsor LLC families are not merged
    assert pc.norm_owner(None, stop) == "" and pc.norm_owner(float("nan"), stop) == ""


def test_single_parcel_host_with_headroom_and_unified_control(cfg):
    parcels = frame([(-500, -500, 1500, 1500, "ACME SOLAR LLC", "F1", 2_000_000)])           # 4 km2 = 988 acres
    r = pc.analyze(ARRAY, parcels, cfg, ac_mw=100.0)
    assert r["parcel_status"] == "ok" and r["n_host_parcels"] == 1
    assert r["parcel_acres_host"] == pytest.approx(4_000_000 / M2)
    assert r["acres_per_mw_parcel"] == pytest.approx(4_000_000 / M2 / 100)
    assert r["headroom_pct_host"] == pytest.approx((4_000_000 / M2 - ARRAY_ACRES) / ARRAY_ACRES)
    assert r["host_cover_share"] == pytest.approx(0.25) and r["unified_land_control"] is True
    assert r["host_owners"] == "ACME SOLAR 100%" and r["parcels_confidence"] == "high"
    assert r["land_value_per_acre"] == pytest.approx(2_000_000 / (4_000_000 / M2)) and r["parcel_vintage"] == "2025"


def test_two_owners_split_the_array_and_break_unified_control(cfg):
    parcels = frame([(-100, -100, 600, 1100, "ACME SOLAR LLC", "F1", 1), (600, -100, 1100, 1100, "J DOE", "D1", 1)])
    r = pc.analyze(ARRAY, parcels, cfg, ac_mw=50.0)
    assert r["n_host_parcels"] == 2 and r["unified_land_control"] is False
    assert r["host_owners"].startswith("ACME SOLAR 58%") and "J DOE 42%" in r["host_owners"]
    assert r["largest_owner_share"] == pytest.approx(700 / 1200)
    assert set(r["land_use_codes"].split(";")) == {"F1", "D1"}


def test_slivers_are_not_hosts_but_a_lone_overlap_is_kept(cfg):
    parcels = frame([(-500, -500, 1500, 1500, "ACME SOLAR LLC", "F1", 1), (995, 0, 1500, 1000, "SLIVER OWNER", "D1", 1)])
    r = pc.analyze(ARRAY, parcels, cfg, ac_mw=10.0)
    assert r["n_host_parcels"] == 1                                       # the 5 m x 1 km sliver (1.2 acres, 0.5% of the array) is dropped
    tiny = frame([(400, 400, 500, 500, "OWNER", "F1", 1)])                # array overlaps only 2.5 acres of one small parcel
    assert pc.analyze(ARRAY, tiny, cfg, 10.0)["n_host_parcels"] == 1      # largest overlap is always kept


def test_same_owner_adjacent_parcels_join_the_unified_holding_other_owners_do_not(cfg):
    parcels = frame([
        (0, 0, 1000, 1000, "ACME SOLAR LLC", "F1", 1),                    # host
        (1000, 0, 2000, 1000, "Acme Solar, L.L.C.", "F1", 1),             # touching, same owner (different spelling)
        (2000, 0, 3000, 1000, "ACME SOLAR LLC", "F1", 1),                 # touches the second one: second round
        (0, 1000, 1000, 2000, "NEIGHBOUR FARMS", "D1", 1),                # touching, other owner
        (0, 3000, 1000, 4000, "ACME SOLAR LLC", "F1", 1),                 # same owner but not touching
    ])
    r = pc.analyze(ARRAY, parcels, cfg, ac_mw=100.0)
    assert r["parcel_acres_host"] == pytest.approx(1_000_000 / M2)
    assert r["adjacent_same_owner_acres"] == pytest.approx(2_000_000 / M2)
    assert r["parcel_acres_unified"] == pytest.approx(3_000_000 / M2)
    assert r["headroom_pct_unified"] == pytest.approx(2.0, rel=1e-6)


def test_status_codes_and_confidence(cfg):
    assert pc.analyze(ARRAY, None, cfg, 10.0)["parcel_status"] == "no_parcel_data"
    assert pc.analyze(ARRAY, frame([]), cfg, 10.0)["parcel_status"] == "no_parcel_data"
    far = frame([(5000, 5000, 6000, 6000, "X", "F1", 1)])
    assert pc.analyze(ARRAY, far, cfg, 10.0)["parcel_status"] == "no_parcel_overlap"
    near = frame([(-500, -500, 1500, 1500, "X", "F1", 1)])
    assert pc.analyze(ARRAY, near, cfg, 10.0, point_fallback=True)["parcels_confidence"] == "low"
    big = frame([(-5000, -5000, 6000, 6000, "X", "F1", 1)])                   # array is 1% of its parcel
    assert pc.analyze(ARRAY, big, cfg, 10.0)["parcels_confidence"] == "medium"
    nameless = frame([(-500, -500, 1500, 1500, None, "F1", 1)])
    r = pc.analyze(ARRAY, nameless, cfg, 10.0)
    assert r["parcel_status"] == "ok" and r["host_owners"] is None and r["unified_land_control"] is False


def _write_source(tmp_path):
    parcels = frame([(-500, -500, 1500, 1500, "ACME SOLAR LLC", "F1", 2_000_000), (3000, 3000, 4000, 4000, "OTHER", "D1", 5)])
    parcels = parcels.rename(columns={"owner": "OWNER_NAME", "prop_id": "PROP_ID", "land_use": "STAT_LAND_USE",
                                      "market_value": "MKT_VALUE", "land_value": "LAND_VALUE", "county": "COUNTY",
                                      "year": "TAX_YEAR"}).drop(columns=["source_file"])
    folder = tmp_path / "manual"
    folder.mkdir()
    parcels.to_crs("EPSG:3081").to_file(folder / "stratmap_48371.gpkg", driver="GPKG")       # Texas Centric Albers, not 5070
    return folder


def test_discover_and_read_neighbourhood_renames_columns_and_reprojects(tmp_path, cfg):
    pytest.importorskip("pyogrio")
    folder = _write_source(tmp_path)
    sources = pc.discover_sources(folder)
    assert len(sources) == 1
    near = pc.read_neighborhood(sources, ARRAY.buffer(3000).bounds, cfg)
    assert len(near) == 2 and set(["owner", "prop_id", "land_use", "market_value", "land_value", "county", "year"]) <= set(near.columns)
    assert near.crs.to_epsg() == 5070 and near["owner"].iloc[0] == "ACME SOLAR LLC"
    only_one = pc.read_neighborhood(sources, ARRAY.buffer(100).bounds, cfg)                   # the far parcel is outside the box
    assert len(only_one) == 1
    elsewhere = pc.read_neighborhood(sources, (500_000, 500_000, 501_000, 501_000), cfg)
    assert elsewhere.empty                                                                    # file does not cover that area


def test_zipped_shapefile_is_discovered(tmp_path):
    pytest.importorskip("pyogrio")
    src = tmp_path / "src"
    src.mkdir()
    frame([(0, 0, 100, 100, "A", "F1", 1)]).drop(columns=["source_file"]).to_file(src / "lp.shp")
    zp = tmp_path / "manual" / "stratmap_48003.zip"
    zp.parent.mkdir()
    with zipfile.ZipFile(zp, "w") as z:
        for f in src.iterdir():
            z.write(f, f.name)
    found = pc.discover_sources(tmp_path / "manual")
    assert len(found) == 1 and found[0][0].startswith("/vsizip/")


def test_loader_columns_match_migration():
    sql = next(Path("supabase/migrations").glob("*_layers_parcels.sql")).read_text(encoding="utf-8")
    body = re.search(r"create table public\.layers_parcels \((.*?)\n\);", sql, re.S).group(1)
    cols = [m.group(1) for line in body.splitlines() if (m := re.match(r"\s*([a-z_0-9]+)\s", line.split("--")[0]))]
    assert [c for c in cols if c != "loaded_at"] == ld.PARCELS_COLUMNS
    row = pc.analyze(ARRAY, frame([(-500, -500, 1500, 1500, "ACME SOLAR LLC", "F1", 1)]), __import__("pipeline.common", fromlist=["load_config"]).load_config(), 100.0)
    row["eia_id"] = 1
    rows = ld.to_rows(pd.DataFrame([row]), ld.PARCELS_COLUMNS, "r1")
    json.dumps(rows)
    assert rows[0]["unified_land_control"] is True and rows[0]["run_id"] == "r1"


def test_run_end_to_end_caches_covered_plants_and_never_caches_missing_coverage(tmp_path, monkeypatch):
    pytest.importorskip("pyogrio")
    raw = tmp_path / "parcels"
    raw.mkdir()
    folder = raw / "manual"
    folder.mkdir()
    src = _write_source(tmp_path)
    for f in src.iterdir():
        f.rename(folder / f.name)
    plants = gpd.GeoDataFrame(
        {"eia_id": [1, 2], "county": ["Pecos", "Ward"], "ac_mw": [100.0, 50.0], "geometry_is_point": [False, True],
         "filter_status": ["pass", "pass"]},
        geometry=[ARRAY, box(500_000, 500_000, 501_000, 501_000).centroid], crs="EPSG:5070")
    monkeypatch.setattr(pc, "raw_dir", lambda s: raw)
    monkeypatch.setattr(pc.g, "load_plants", lambda: plants)
    written = {}
    monkeypatch.setattr(pc.g, "write_layer", lambda name, df: written.setdefault(name, df))
    pc.run()
    df = written["parcels"].set_index("eia_id")
    assert df.loc[1, "parcel_status"] == "ok" and df.loc[1, "parcels_confidence"] in ("high", "medium")   # cover is ~0.25: reprojection decides
    assert df.loc[2, "parcel_status"] == "no_parcel_data"                 # no file covers Ward County
    assert (raw / "selected" / "1.parquet").exists() and not (raw / "selected" / "2.parquet").exists()
    for f in folder.iterdir():                                            # the big source can be deleted once selections are cached
        f.unlink()
    written.clear()
    pc.run()
    assert written["parcels"].set_index("eia_id").loc[1, "parcel_status"] == "ok"
