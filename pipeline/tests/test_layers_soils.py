"""Phase 4 soils (SSURGO): Cote & Konrad lambda, depth weighting, plant summary, scoring, loader."""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.common import load_config
from pipeline.phase_4_geo import load as ld
from pipeline.phase_4_geo import soils as so
from pipeline.phase_5_score import score as sc


@pytest.fixture
def cfg():
    return load_config()


def test_lambda_silty_clay_loam_hand_check(cfg):
    # Reagan silty clay loam (Midway, Pecos): sand 6.9 %, rho_b 1.43, theta_fc 32.1 % -> ~1.25 W/m-K moist
    lam = so.lambda_cote_konrad(6.9, 31.0, 1.43, 32.1, cfg["layers"]["soils"]["model"])
    assert float(lam["moist"]) == pytest.approx(1.25, abs=0.03)
    assert float(lam["dry"]) == pytest.approx(0.21, abs=0.01) and float(lam["sat"]) == pytest.approx(1.48, abs=0.03)


def test_lambda_orders_sand_above_clay_and_wet_above_dry(cfg):
    m = cfg["layers"]["soils"]["model"]
    sand = float(so.lambda_cote_konrad(85, 5, 1.60, 15, m)["moist"])
    clay = float(so.lambda_cote_konrad(20, 45, 1.35, 35, m)["moist"])
    dry_sand = float(so.lambda_cote_konrad(85, 5, 1.60, 2, m)["moist"])
    assert sand > clay and sand > 1.5 and dry_sand < sand


def raw(horizons, mapunits=None, restrictions=None, aggregates=None):
    return {"mapunits": mapunits or [{"mukey": "1", "a": 1.0}], "horizons": horizons, "restrictions": restrictions or [],
            "aggregates": aggregates or [], "fetched": "2026-10-05"}


def hz(mukey, cokey, comp, pct, top, bot, sand=30, clay=25, db=1.45, wfc=25):
    return {"mukey": mukey, "muname": f"MU {mukey}", "cokey": cokey, "compname": comp, "comppct_r": pct, "hzdept_r": top,
            "hzdepb_r": bot, "sandtotal_r": sand, "claytotal_r": clay, "dbthirdbar_r": db, "wthirdbar_r": wfc}


def test_summary_weights_depth_components_and_rock_outcrop(cfg):
    m = cfg["layers"]["soils"]["model"]
    h = [hz("1", "a", "Loamy", 60, 0, 100, sand=30), hz("1", "a", "Loamy", 60, 100, 300, sand=80, clay=5, db=1.6, wfc=15),
         {"mukey": "1", "muname": "MU 1", "cokey": "b", "compname": "Rock outcrop", "comppct_r": 40, "hzdept_r": None,
          "hzdepb_r": None, "sandtotal_r": None, "claytotal_r": None, "dbthirdbar_r": None, "wthirdbar_r": None}]
    s = so.summarize(raw(h, restrictions=[{"mukey": "1", "cokey": "a", "comppct_r": 60, "reskind": "Petrocalcic", "resdept_r": 45}],
                         aggregates=[{"mukey": "1", "brockdepmin": None}]), cfg)
    top = so.lambda_cote_konrad(30, 25, 1.45, 25, m)["moist"]
    deep = so.lambda_cote_konrad(80, 5, 1.6, 15, m)["moist"]
    assert s["soil_status"] == "ok" and s["soil_data_share"] == pytest.approx(0.6)        # rock outcrop has no data
    assert s["soil_lambda_w_mk"] == pytest.approx((float(top) + float(deep)) / 2, abs=1e-3)  # 100 cm each inside 0-200
    assert s["restriction_kinds"] == "Petrocalcic" and s["restriction_share"] == pytest.approx(0.6)
    assert s["restriction_min_depth_cm"] == 45 and np.isnan(s["bedrock_depth_cm_min"])
    assert s["dominant_soil"] == "Loamy (60%)"


def test_summary_area_weights_mapunits(cfg):
    h = [hz("1", "a", "Sandy", 100, 0, 200, sand=85, clay=5, db=1.6, wfc=15), hz("2", "b", "Clayey", 100, 0, 200, sand=10, clay=50)]
    s = so.summarize(raw(h, mapunits=[{"mukey": "1", "a": 3.0}, {"mukey": "2", "a": 1.0}]), cfg)
    m = cfg["layers"]["soils"]["model"]
    exp = 0.75 * float(so.lambda_cote_konrad(85, 5, 1.6, 15, m)["moist"]) + 0.25 * float(so.lambda_cote_konrad(10, 50, 1.45, 25, m)["moist"])
    assert s["soil_lambda_w_mk"] == pytest.approx(exp, abs=1e-3) and s["n_mapunits"] == 2 and s["dominant_soil"] == "Sandy (75%)"


def test_summary_without_data(cfg):
    assert so.summarize(raw([], mapunits=[]), cfg)["soil_status"] == "no_soil_data"


def test_lambda_informative_only_but_sets_trt_flag(cfg):
    base = dict(eia_id=1, plant_name="P", county="Pecos", tier="T1b", ac_mw=200.0, filter_status="pass", lat=31.0, lon=-103.0,
                metrics_status="ok", capture_rate_potential=0.65, curtailment_pct=0.08, sced_net_cf=0.24, net_ac_cf=0.24,
                cod_first=pd.Timestamp("2017-06-01"), module_tech="c-Si", tracking_type="single_axis", bifacial_share=np.nan,
                parcel_status="ok", parcels_confidence="high", acres_per_mw_parcel=12.0, headroom_pct_unified=0.6,
                unified_land_control=True, grid_voltage_kv=345.0, transmission_source="osm", dist_345kv_sub_mi=2.0,
                firm_it_mw=130.0, flood_flag=None, flood_status="not_mapped", distribution_class_poi=False, sb6_review_required=True)
    rows = [base | {"eia_id": i, "soil_lambda_w_mk": v} for i, v in enumerate([2.1, 1.6, 1.2, 0.8, np.nan], 1)]
    out, notes = sc.score(pd.DataFrame(rows), cfg)
    o = out.set_index("eia_id")
    # owner decision 5 Oct 2026: no points, no completeness weight, every plant's E unaffected by lambda
    assert o["pts_lambda"].isna().all() and o["score_E"].nunique() == 1
    assert "lambda" not in notes["max_points"] and notes["positive_max_total"] == 94
    assert list(o["thermal_response_test_required"].iloc[:4]) == [False, False, False, True]
    assert pd.isna(o.loc[5, "thermal_response_test_required"])
    # and the bands come back if the config flag is flipped
    on = {**cfg, "scoring": {**cfg["scoring"], "E_thermal_cooling": {**cfg["scoring"]["E_thermal_cooling"],
          "soil_lambda_w_mk": {**cfg["scoring"]["E_thermal_cooling"]["soil_lambda_w_mk"], "scored": True}}}}
    o2 = sc.score(pd.DataFrame(rows), on)[0].set_index("eia_id")
    assert list(o2["pts_lambda"]) == [6, 4, 2, 0, 3]


def test_loader_columns_match_migration():
    sql = next(Path("supabase/migrations").glob("*_layers_soils.sql")).read_text(encoding="utf-8")
    body = re.search(r"create table public\.layers_soils \((.*?)\n\);", sql, re.S).group(1)
    cols = [m.group(1) for line in body.splitlines() if (m := re.match(r"\s*([a-z_0-9]+)\s", line.split("--")[0]))]
    assert [c for c in cols if c != "loaded_at"] == ld.SOILS_COLUMNS
