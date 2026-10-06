"""Phase 4 load-pocket layer: table validation, what qualifies, firm vs announced distances, scoring, loader columns."""
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from pipeline.common import load_config
from pipeline.phase_4_geo import common as g
from pipeline.phase_4_geo import load as ld
from pipeline.phase_4_geo import load_pocket as lp
from pipeline.phase_5_score import score as sc

MI_DEG = 1 / 69.0     # ~1 mile of latitude in degrees


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def plants():
    # two small arrays near Fort Stockton (Pecos) and one near Abilene
    return gpd.GeoDataFrame({"eia_id": [1, 2, 3], "lat": [30.9, 31.5, 32.45], "lon": [-102.9, -103.5, -99.73]},
                            geometry=[box(-102.91, 30.89, -102.89, 30.91), box(-103.51, 31.49, -103.49, 31.51),
                                      box(-99.74, 32.44, -99.72, 32.46)], crs="EPSG:4326")


def row(pid, kind="data_center", status="operating", mw=200.0, lat=30.9, lon=-102.9, **kw):
    return {"project_id": pid, "name": pid.upper(), "developer": None, "kind": kind, "status": status, "load_mw": mw,
            "load_mw_basis": None, "county": None, "city": None, "lat": lat, "lon": lon, "location_confidence": "city",
            "source_url": "https://example.org/x", "source_date": "2026-09", "notes": None, "added_by": "CR",
            "added_on": "2026-10-06", "verified_by": None, "verified_on": None} | kw


def test_validate_flags_bad_rows():
    df = pd.DataFrame([row("a"), row("a"), row("b", kind="bitcoin"), row("c", status="rumour"), row("d", lat=np.nan),
                       row("e", lat=40.0), row("f", source_url=None), row("g", location_confidence="exact")])
    errs = "\n".join(lp.validate(df))
    for needle in ("duplicate project_id: a", "b: kind", "c: status", "d: lat/lon missing", "e: lat/lon", "f: source_url",
                   "g: location_confidence"):
        assert needle in errs
    assert lp.validate(pd.DataFrame([row("ok")])) == []


def test_qualifying_rules(cfg):
    c = cfg["layers"]["load_pocket"]
    df = pd.DataFrame([row("dc_small", mw=10.0), row("dc_nomw", mw=np.nan), row("btc_big", kind="crypto", mw=300.0),
                       row("btc_small", kind="crypto", mw=50.0), row("btc_nomw", kind="crypto", mw=np.nan),
                       row("dead", status="cancelled"), row("ann", status="announced")])
    assert set(lp.qualifying(df, c)["project_id"]) == {"dc_small", "dc_nomw", "btc_big", "ann"}


def test_compute_firm_and_announced(cfg, plants):
    # firm project ~5 mi north of plant 1; announced one ~20 mi from plant 2; nothing near Abilene
    projects = pd.DataFrame([row("firm", lat=30.9 + 0.01 + 5 * MI_DEG, lon=-102.9),
                             row("ann", status="announced", mw=1000.0, lat=31.5 + 0.01 + 20 * MI_DEG, lon=-103.5)])
    out = lp.compute(plants, projects, cfg, "2026-10-06").set_index("eia_id")
    assert out.loc[1, "dist_load_pocket_firm_mi"] == pytest.approx(5, abs=0.3)
    assert out.loc[1, "nearest_firm_project"] == "FIRM" and out.loc[1, "nearest_firm_status"] == "operating"
    assert out.loc[2, "dist_load_pocket_announced_mi"] == pytest.approx(20, abs=0.5)
    assert np.isnan(out.loc[3, "dist_load_pocket_firm_mi"]) and np.isnan(out.loc[3, "dist_load_pocket_announced_mi"])
    assert out.loc[3, "n_projects_within_search"] == 0 and out.loc[3, "projects_within_search"] is None
    assert out.loc[1, "n_projects_within_search"] == 1 and out.loc[1, "projects_within_search"].startswith("FIRM (operating, 200 MW")
    assert out.loc[2, "load_pocket_location_confidence"] == "city"
    assert (out["load_pocket_confidence"] == "manual").all()


def test_compute_with_empty_table(cfg, plants):
    out = lp.compute(plants, pd.DataFrame([row("x")]).iloc[0:0], cfg, None)
    assert out["dist_load_pocket_firm_mi"].isna().all() and (out["n_projects_within_search"] == 0).all()


def score_rows(cfg, **layer):
    base = dict(eia_id=1, plant_name="P", county="Pecos", tier="T1b", ac_mw=200.0, filter_status="pass", lat=31.0, lon=-103.0,
                metrics_status="ok", capture_rate_potential=0.65, curtailment_pct=0.08, sced_net_cf=0.24, net_ac_cf=0.24,
                cod_first=pd.Timestamp("2017-06-01"), module_tech="c-Si", tracking_type="single_axis", bifacial_share=np.nan,
                parcel_status="ok", parcels_confidence="high", acres_per_mw_parcel=12.0, headroom_pct_unified=0.6,
                unified_land_control=True, grid_voltage_kv=345.0, transmission_source="osm", dist_345kv_sub_mi=2.0,
                firm_it_mw=130.0, flood_flag=None, flood_status="not_mapped", distribution_class_poi=False, sb6_review_required=True)
    df = pd.DataFrame([base | {"eia_id": i} | {k: v[i - 1] for k, v in layer.items()} for i in range(1, 1 + len(next(iter(layer.values()))))])
    return sc.score(df, cfg)[0].set_index("eia_id")


def test_score_bands_and_announced_discount(cfg):
    out = score_rows(cfg, dist_load_pocket_firm_mi=[3.0, 18.0, 40.0, np.nan, np.nan, 20.0],
                     dist_load_pocket_announced_mi=[np.nan, 2.0, np.nan, 8.0, np.nan, 5.0],
                     load_pocket_source=["manual"] * 6)
    # 5; max(3, 0.5×5); 1; announced only 0.5×5; nothing within 50 mi = measured 0; max(3, 2.5)
    assert list(out["pts_load_pocket"]) == [5, 3, 1, 2.5, 0, 3]
    assert "load_pocket" not in (out.loc[5, "missing_inputs"] or "")


def test_score_neutral_without_layer(cfg):
    out = score_rows(cfg, grid_voltage_kv=[345.0])
    assert out.loc[1, "pts_load_pocket"] == 2.5 and "load_pocket" in out.loc[1, "missing_inputs"]


def test_loader_columns_match_migration():
    sql = next(Path("supabase/migrations").glob("*_layers_load_pocket.sql")).read_text(encoding="utf-8")
    body = re.search(r"create table public\.layers_load_pocket \((.*?)\n\);", sql, re.S).group(1)
    cols = [m.group(1) for line in body.splitlines() if (m := re.match(r"\s*([a-z_0-9]+)\s", line.split("--")[0]))]
    assert [c for c in cols if c != "loaded_at"] == ld.LOAD_POCKET_COLUMNS


def test_committed_table_is_valid(cfg):
    path = lp.table_path(cfg)
    if not path.exists():
        pytest.skip("data/load_pockets.csv not created yet")
    assert lp.validate(lp.read_table(path)) == []
