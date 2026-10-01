import pandas as pd
import pytest

from pipeline.phase_2_normalize import county_check
from pipeline.phase_2_normalize.plant_master import assign_tier, build, resolve_tiers, sb6_line_ac_mw


@pytest.fixture
def built(generators, polygons, cfg, as_of):
    plants, orphans = build(generators, polygons, cfg, as_of)
    return plants.set_index("eia_id"), orphans


def test_only_texas_pv_plants(built):
    plants, _ = built
    assert sorted(plants.index) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_rollup_and_ilr(built):
    p = built[0].loc[1]
    assert p.ac_mw == 80 and p.n_generators == 2
    assert p.dc_mw == 104 and p.dc_mw_source == "eia860m"
    assert p.ilr == pytest.approx(1.30)
    assert p.cod_first == pd.Timestamp("2017-06-01") and p.cod_multi_phase
    assert p.has_colocated_storage


def test_dc_fallback_to_uspvdb(built):
    p = built[0].loc[2]
    assert p.dc_mw_source == "uspvdb" and p.ilr == pytest.approx(1.30)


def test_footprint(built):
    p = built[0].loc[1]
    assert p.array_acres == pytest.approx(640)
    assert p.array_acres_calc == pytest.approx(640, rel=1e-3)  # recomputed in EPSG:5070
    assert p.acres_per_mw_ac == pytest.approx(8.0)


@pytest.mark.parametrize("eid,status,reason", [
    (1, "pass", ""),
    (2, "pass", ""),
    (3, "fail", "ac_mw<10"),
    (4, "fail", "cod>2022-12-31"),
    (5, "review", "array_acres_per_mw<5"),   # array area only flags (footprint_basis: array_flag)
    (6, "review", "no_uspvdb_polygon"),
    (7, "pass", ""),
    (8, "pass", ""),
])
def test_filters(built, eid, status, reason):
    p = built[0].loc[eid]
    assert p.filter_status == status
    assert reason in p.filter_reasons


def test_non_ercot_flagged_not_dropped(built):
    p = built[0].loc[7]
    assert p.non_ercot_texas and p.filter_status == "pass"
    assert not built[0].loc[1].non_ercot_texas


def test_tiers_and_sb6(built):
    plants = built[0]
    assert plants.loc[1].tier == "T1a" and not plants.loc[1].sb6_review_required   # 59.8 MW load
    assert plants.loc[2].tier == "T1b" and plants.loc[2].sb6_review_required       # 112.1 MW load
    # 100 MW AC at ILR 1.30 → 74.75 MW load: below the derived 100.33 MW line, so T1a and no SB6 — consistent
    assert plants.loc[8].tier == "T1a"
    assert plants.loc[8].planned_load_mw == pytest.approx(74.75)
    assert not plants.loc[8].sb6_review_required


def test_sb6_line_derived_from_config(cfg):
    assert sb6_line_ac_mw(cfg) == pytest.approx(75 / (1.15 * 0.5 * 1.30))   # 100.334 MW AC
    moved = {**cfg, "assumptions": {**cfg["assumptions"], "pue_assumed": 1.25}}
    assert sb6_line_ac_mw(moved) == pytest.approx(92.31, abs=0.01)           # higher PUE pulls the line down


def test_tier_boundaries(cfg):
    s = pd.Series([9.99, 10, 24.99, 25, 74.99, 75, 100, 100.33, 100.34, 500])
    assert list(assign_tier(s, resolve_tiers(cfg))) == [
        pd.NA, "T3", "T3", "T2", "T2", "T1a", "T1a", "T1a", "T1b", "T1b"]


def test_default_ilr_plant_tier_matches_sb6(cfg, as_of):
    """At the default ILR, tier T1b ⇔ sb6_review_required, on both sides of the line."""
    from pipeline.phase_2_normalize.plant_master import apply_filters
    line = sb6_line_ac_mw(cfg)
    df = pd.DataFrame({
        "ac_mw": [line - 0.01, line + 0.01], "dc_mw_eia": [float("nan")] * 2, "dc_mw_uspvdb": [float("nan")] * 2,
        "array_acres": [800.0] * 2, "cod_first": [pd.Timestamp("2018-01-01")] * 2,
        "cod_last": [pd.Timestamp("2018-01-01")] * 2, "ba_code": ["ERCO"] * 2,
        "ac_mw_uspvdb": [100.0] * 2, "array_acres_calc": [800.0] * 2,
    })
    out = apply_filters(df, cfg, as_of)
    assert list(out["tier"]) == ["T1a", "T1b"]
    assert list(out["sb6_review_required"]) == [False, True]


def test_parcel_basis_fails_on_acreage(generators, polygons, cfg, as_of):
    parcel = {**cfg, "hard_filters": {**cfg["hard_filters"], "footprint_basis": "parcel"}}
    plants, _ = build(generators, polygons, parcel, as_of)
    p = plants.set_index("eia_id").loc[5]
    assert p.filter_status == "fail" and p.filter_reasons == "acres_per_mw<5"


def test_cod_caveat_flags(built):
    plants = built[0]
    assert not plants.loc[2].tax_equity_consent_likely       # 2016
    assert plants.loc[5].tax_equity_consent_likely           # 2019
    # ITC recapture window = COD within 5 years of as_of (2026-09-30 → after 2021-09-30)
    assert not plants.loc[7].itc_recapture_open              # 2021-06
    assert plants.loc[4].itc_recapture_open                  # 2023-02
    assert not plants.loc[1].itc_recapture_open


def test_orphans(built):
    _, orphans = built
    assert list(orphans["eia_id"]) == [99]
    assert orphans.attrs["tx_rows_without_eia_id"] == 1


def test_county_report(built, tmp_path):
    plants, orphans = built
    md = county_check.report(plants.reset_index(), orphans, "Pecos", tmp_path)
    text = md.read_bytes().decode("utf-8")  # explicit: Windows defaults to cp1252, which has no Δ
    assert "for Pecos: **7**, 468.0 MW AC" in text   # plants 1,2,3,4,5,6,8
    assert "Pass all hard filters: **3**, 330.0 MW AC" in text  # 1, 2, 8
    assert "| review | 2 | 70.0 |" in text      # Foxtrot (no polygon) + Echo (array acreage)
    assert "T1a/T1b line (derived): 100.33 MW AC" in text
    assert "not in EIA-860M operating: 1" in text
    assert "| pass | 3 | 330.0 |" in text        # integer plant counts, not 3.0
    assert md.read_bytes().startswith(b"\xef\xbb\xbf")
    assert (tmp_path / "pecos_handcheck.csv").exists()
