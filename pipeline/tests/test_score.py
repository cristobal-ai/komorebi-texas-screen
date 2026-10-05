"""Phase 5 scoring: bands, quantile direction (thesis inversion), neutral missing inputs, completeness, ranks."""
import numpy as np
import pandas as pd
import pytest

from pipeline.common import load_config
from pipeline.phase_5_score import score as sc


@pytest.fixture
def cfg():
    return load_config()


def test_band_first_match_and_nan():
    bands = [{"gte": 345, "pts": 10}, {"gte": 138, "pts": 6}, {"gte": 69, "pts": 2}, {"else": 0}]
    assert sc.band(345, bands) == 10 and sc.band(138, bands) == 6 and sc.band(34.5, bands) == 0
    assert np.isnan(sc.band(np.nan, bands)) and np.isnan(sc.band(None, bands))


def test_quantile_points_reward_low_capture_and_high_curtailment():
    fleet = pd.Series(np.linspace(0.5, 0.9, 101))            # q25 0.6, q50 0.7, q75 0.8
    cap = sc.quantile_points(pd.Series([0.55, 0.65, 0.75, 0.85, np.nan]), fleet, {"q25": 12, "q50": 8, "q75": 4, "else": 0}, False)
    assert cap.tolist()[:4] == [12, 8, 4, 0] and np.isnan(cap.iloc[4])
    cur = sc.quantile_points(pd.Series([0.85, 0.75, 0.55]), fleet, {"q75": 8, "q50": 5, "else": 2}, True)
    assert cur.tolist() == [8, 5, 2]


def test_region_of(cfg):
    r = cfg["benchmarks"]["regions"]
    assert sc.region_of(31.0, -103.0, r) == "west_texas"
    assert sc.region_of(29.3, -95.4, r) == "coastal"
    assert sc.region_of(31.5, -97.0, r) == "central_texas"


def plant(**kw):
    base = dict(eia_id=1, plant_name="P", county="Pecos", tier="T1b", ac_mw=200.0, filter_status="pass", lat=31.0, lon=-103.0,
                metrics_status="ok", capture_rate_potential=0.65, curtailment_pct=0.08, sced_net_cf=0.24, net_ac_cf=0.24,
                cod_first=pd.Timestamp("2017-06-01"), module_tech="c-Si", tracking_type="single_axis", bifacial_share=np.nan,
                parcel_status="ok", parcels_confidence="high", acres_per_mw_parcel=12.0, headroom_pct_unified=0.6,
                unified_land_control=True, grid_voltage_kv=345.0, transmission_source="osm", dist_345kv_sub_mi=2.0,
                firm_it_mw=130.0, flood_flag=None, flood_status="not_mapped", distribution_class_poi=False, sb6_review_required=True)
    return base | kw


def run(cfg, rows):
    return sc.score(pd.DataFrame(rows), cfg)


def test_low_performer_outscores_good_performer(cfg):
    rows = [plant(eia_id=i, capture_rate_potential=c, curtailment_pct=k, sced_net_cf=f)
            for i, (c, k, f) in enumerate([(0.55, 0.20, 0.18), (0.62, 0.10, 0.22), (0.68, 0.05, 0.25), (0.75, 0.01, 0.28)], 1)]
    out, _ = run(cfg, rows)
    a = out.set_index("eia_id")["score_A"]
    assert a[1] > a[2] > a[4]
    assert out.set_index("eia_id").loc[1, "rank_overall"] == 1


def test_missing_metrics_score_neutral_and_lower_completeness(cfg):
    rows = [plant(eia_id=1), plant(eia_id=2, capture_rate_potential=0.5), plant(eia_id=3, metrics_status="no_sced_data",
                                                                               sced_net_cf=np.nan, net_ac_cf=np.nan)]
    out, notes = run(cfg, rows)
    p3 = out.set_index("eia_id").loc[3]
    assert p3["pts_capture"] == 6 and p3["pts_curtailment"] == 4 and p3["pts_cf_benchmark"] == 3
    assert "capture" in p3["missing_inputs"] and p3["data_completeness"] < out.set_index("eia_id").loc[1, "data_completeness"]
    # layers not built yet: neutral half points
    assert p3["score_E"] == 7.5 and p3["pts_load_pocket"] == 2.5
    assert set(notes["not_built"]) >= {"lambda", "load_pocket"}


def test_no_345_within_radius_is_data_not_missing(cfg):
    out, _ = run(cfg, [plant(eia_id=1, dist_345kv_sub_mi=np.nan), plant(eia_id=2, transmission_source=None, dist_345kv_sub_mi=np.nan)])
    o = out.set_index("eia_id")
    assert o.loc[1, "pts_dist_345"] == 0 and "dist_345" not in (o.loc[1, "missing_inputs"] or "")
    assert o.loc[2, "pts_dist_345"] == 2.5 and "dist_345" in o.loc[2, "missing_inputs"]


def test_vintage_proxy_for_section_b(cfg):
    out, _ = run(cfg, [plant(eia_id=1, cod_first=pd.Timestamp("2017-01-01")),
                       plant(eia_id=2, cod_first=pd.Timestamp("2020-01-01")),
                       plant(eia_id=3, cod_first=pd.Timestamp("2022-01-01")),
                       plant(eia_id=4, cod_first=pd.Timestamp("2022-01-01"), module_tech="thin_film_CdTe", tracking_type="fixed")])
    b = out.set_index("eia_id")["score_B"]
    assert b[1] == 15 and b[2] == 7.5 and b[3] == 0
    assert b[4] == 5 + 4                                      # CdTe monofacial, fixed tilt; not polycrystalline


def test_flood_flag_does_not_change_score(cfg):
    out, _ = run(cfg, [plant(eia_id=1, flood_flag=True, flood_status="ok"), plant(eia_id=2, flood_flag=False, flood_status="ok")])
    o = out.set_index("eia_id")
    assert o.loc[1, "score_total"] == o.loc[2, "score_total"] and bool(o.loc[1, "flood_flag"]) is True


def test_fixed_cost_drag_hits_small_plants(cfg):
    out, _ = run(cfg, [plant(eia_id=1, firm_it_mw=130.0), plant(eia_id=2, firm_it_mw=6.5, tier="T3")])
    o = out.set_index("eia_id")
    assert o.loc[1, "pts_fixed_cost"] == 0 and o.loc[2, "pts_fixed_cost"] == -10      # $2.4M / 6.5 MW ≈ $369/kW
    assert not o.loc[2, "fixed_cost_includes_fiber"]


def test_review_plants_are_scored_not_ranked(cfg):
    out, _ = run(cfg, [plant(eia_id=1), plant(eia_id=2, filter_status="review")])
    o = out.set_index("eia_id")
    assert pd.isna(o.loc[2, "rank_overall"]) and o.loc[1, "rank_overall"] == 1 and o.loc[2, "score_total"] > 0


def test_sections_sum_to_total(cfg):
    out, _ = run(cfg, [plant(eia_id=i, capture_rate_potential=0.5 + i / 20) for i in range(5)])
    assert np.allclose(out[[f"score_{k}" for k in sc.SECTIONS]].sum(axis=1), out["score_total"])
    comps = [f"pts_{c}" for cs in sc.SECTIONS.values() for c in cs]
    assert np.allclose(out[comps].sum(axis=1), out["score_total"])


def test_loader_columns_match_migration_and_scores(cfg):
    import re
    from pathlib import Path

    from pipeline.phase_5_score import load as sl
    sql = next(Path("supabase/migrations").glob("*_plant_scores.sql")).read_text(encoding="utf-8")
    body = re.search(r"create table public\.plant_scores \((.*?)\n\);", sql, re.S).group(1)
    cols = [m.group(1) for line in body.splitlines() if (m := re.match(r"\s*([a-z_0-9]+)\s", line.split("--")[0]))]
    assert [c for c in cols if c != "loaded_at"] == [c.lower() for c in sl.SCORE_COLUMNS]
    out, _ = run(cfg, [plant(eia_id=1)])
    out["score_version"], out["run_id"] = "v", "r"
    assert not [c for c in sl.SCORE_COLUMNS if c not in out.columns]
