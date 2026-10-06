"""Section A offtake: table validation, contract-end parsing, classes and points (owner decisions 6 Oct 2026)."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from pipeline.common import load_config
from pipeline.phase_5_score import offtake as ot
from pipeline.phase_5_score import score as sc

ASOF = dt.date(2026, 10, 6)


@pytest.fixture
def spec():
    return load_config()["scoring"]["A_acquisition_discount"]["offtake_status"]


def row(eia_id, typ="ppa", ig="yes", end="2040", **kw):
    return {"eia_id": eia_id, "plant_name": f"P{eia_id}", "offtake_type": typ, "counterparty": "Buyer",
            "counterparty_ig": ig, "contract_start": "2018", "contract_end": end, "share_contracted": None,
            "source_url": "https://example.org", "source_date": "2026-03", "confidence": "high", "notes": None,
            "contract_end_basis": None, "flag": None,
            "verified_by": None, "verified_on": None} | kw


def table(rows):
    df = pd.DataFrame(rows)
    df["eia_id"] = df["eia_id"].astype("Int64")
    return df


def test_parse_end():
    assert ot.parse_end("2035", 7) == dt.date(2035, 7, 1)
    assert ot.parse_end("2035-06", 7) == dt.date(2035, 6, 1)
    assert ot.parse_end("2035-06-30", 7) == dt.date(2035, 6, 30)
    assert ot.parse_end("mid 2035", 7) is None and ot.parse_end(None, 7) is None


def test_classify_points(spec):
    df = table([row(1), row(2, ig="no"), row(3, ig="unknown"), row(4, end="2029"), row(5, typ="hedge", end="2028-12"),
                row(6, typ="hedge", end="2039", ig="no"), row(7, end="2024"), row(8, typ="merchant", ig=None, end=None),
                row(9, typ="utility_owned", end=None), row(10, typ="unknown", end=None, source_url=None, confidence=None),
                row(11, end=None)])
    out = ot.classify(df, spec, ASOF).set_index("eia_id")
    assert list(out["offtake_status"]) == [
        "long_contract_ig", "long_contract_non_ig", "long_contract_unknown_credit", "short_contract", "short_contract",
        "long_contract_non_ig", "merchant", "merchant", "utility_owned", "unknown", "unknown"]
    pts = out["offtake_pts"]
    assert list(pts.iloc[:9]) == [0, 1, 0.5, 3, 3, 1, 4, 4, 0]
    assert np.isnan(pts.loc[10]) and np.isnan(pts.loc[11])      # nothing found / end not published: neutral
    assert bool(out.loc[7, "offtake_expired"]) and out.loc[4, "offtake_years_left"] == pytest.approx(2.74, abs=0.01)


def test_validate():
    df = table([row(1), row(1), row(2, typ="tolling"), row(3, ig="maybe"), row(4, source_url=None), row(5, end="soon"),
                row(6, typ="unknown", source_url=None, confidence=None)])
    errs = "\n".join(ot.validate(df))
    for needle in ("duplicate eia_id: 1", "2 P2: offtake_type", "3 P3: counterparty_ig", "4 P4: source_url",
                   "5 P5: contract_end"):
        assert needle in errs
    assert "6 P6" not in errs


def test_score_uses_table_and_counts_completeness(spec):
    cfg = load_config()
    off = ot.classify(table([row(1, typ="merchant", end=None), row(2, typ="unknown", end=None, source_url=None, confidence=None)]),
                      spec, ASOF)
    base = dict(plant_name="P", county="Pecos", tier="T1b", ac_mw=200.0, filter_status="pass", lat=31.0, lon=-103.0,
                metrics_status="ok", capture_rate_potential=0.65, curtailment_pct=0.08, sced_net_cf=0.24, net_ac_cf=0.24,
                cod_first=pd.Timestamp("2017-06-01"), module_tech="c-Si", tracking_type="single_axis", bifacial_share=np.nan,
                parcel_status="ok", parcels_confidence="high", acres_per_mw_parcel=12.0, headroom_pct_unified=0.6,
                unified_land_control=True, grid_voltage_kv=345.0, transmission_source="osm", dist_345kv_sub_mi=2.0,
                firm_it_mw=130.0, flood_flag=None, flood_status="not_mapped", distribution_class_poi=False, sb6_review_required=True)
    plants = pd.DataFrame([base | {"eia_id": 1}, base | {"eia_id": 2}, base | {"eia_id": 3}])
    df = plants.merge(off, on="eia_id", how="left")
    out, _ = sc.score(df, cfg)
    o = out.set_index("eia_id")
    assert list(o["pts_offtake"]) == [4, 2, 2]
    assert "offtake" not in (o.loc[1, "missing_inputs"] or "") and "offtake" in o.loc[2, "missing_inputs"]
    assert list(o["offtake_status"]) == ["merchant", "unknown", "unknown"]
    assert list(o["offtake_confidence"]) == ["high", "unknown", "unknown"]


def test_affiliate_assumed_end_and_flags(spec):
    cod = pd.Series({1: pd.Timestamp("2018-03-01"), 2: pd.Timestamp("2020-06-01"), 3: pd.Timestamp("2019-01-01"),
                     4: pd.Timestamp("2019-01-01"), 5: pd.Timestamp("2019-01-01")})
    df = table([row(1, end=None), row(2, typ="hedge", end=None), row(3, typ="affiliate", ig=None, end=None),
                row(4, end="2032", share_contracted=0.2, counterparty="Unnamed company", ig="unknown", confidence="low"),
                row(5, flag="buyer in bankruptcy")])
    out = ot.classify(df, spec, ASOF, cod).set_index("eia_id")
    assert out.loc[1, "offtake_contract_end"] == "2033-03" and out.loc[1, "offtake_end_basis"] == "assumed"
    assert out.loc[1, "offtake_status"] == "long_contract_ig"                       # 2018 + 15 = 2033: 6.4 yr left
    assert out.loc[2, "offtake_contract_end"] == "2032-06" and out.loc[2, "offtake_status"] == "long_contract_ig"
    assert out.loc[3, "offtake_status"] == "affiliate" and out.loc[3, "offtake_pts"] == spec["affiliate"]
    f4 = out.loc[4, "offtake_flags"]
    for needle in ("only 20% of output", "counterparty not named", "crosses the 5-yr line", "low-confidence source"):
        assert needle in f4
    assert "assumed COD + 15 yr" in out.loc[1, "offtake_flags"] and "assumed COD + 12 yr" in out.loc[2, "offtake_flags"]
    assert out.loc[5, "offtake_flags"] == "buyer in bankruptcy"


def test_committed_table_is_valid(spec):
    path = ot.table_path(spec)
    if not path.exists():
        pytest.skip("data/offtake.csv not created yet")
    assert ot.validate(ot.read_table(path)) == []
