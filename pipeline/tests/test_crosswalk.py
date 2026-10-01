import openpyxl
import pandas as pd
import pytest

from pipeline.phase_1_ingest.ercot import read_cdr, summarize_sced_pv
from pipeline.phase_2_normalize.plant_master import build as build_plants
from pipeline.phase_3_market.crosswalk import CSV_COLUMNS, build, norm_name, review_report

HDR = ["UNIT NAME", "GENERATION INTERCONNECTION PROJECT CODE", "UNIT CODE", "COUNTY", "FUEL", "ZONE",
       "IN SERVICE YEAR", "INSTALLED CAPACITY RATING"]
UNITS = [
    ["Alpha Solar 1", "16INR0001", "ALPHA_UNIT1", "PECOS", "SOLAR", "WEST", 2017, 50],
    ["Alpha Solar II", "17INR0002", "ALPHA_UNIT2", "PECOS", "SOLAR", "WEST", 2018, 30],
    ["Bravo Solar", "15INR0003", "BRAVO_SLR", "PECOS", "SOLAR", "WEST", 2016, 150],
    [None, "18INR0004", "ECHOX_UNIT1", "PECOS", "SOLAR", "WEST", 2019, 30],      # code only, no name
    ["Golf Solar", "20INR0005", "GOLF_SOLAR1", "EL PASO", "SOLAR", "FAR WEST", 2021, 20],
    ["Hotel Solar", "14INR0006", "HOTEL_SOLAR1", "REEVES", "SOLAR", "WEST", 2015, 100],  # wrong county
    ["Zephyr Wind", "14INR0007", "ZEPHYR_WIND1", "PECOS", "WIND", "WEST", 2015, 200],    # not solar
]


@pytest.fixture
def cdr_raw(tmp_path, cfg):
    wb = openpyxl.Workbook()
    wb.active.title = "Summary"
    wb.active.append(["Capacity, Demand and Reserves — summary only"])
    ws = wb.create_sheet("Existing Resources")
    ws.append(["ERCOT CDR December 2025"])
    ws.append(["Unit details"])
    ws.append(HDR)
    for u in UNITS:
        ws.append(u)
    path = tmp_path / "CapacityDemandandReserveReport_Dec2025.xlsx"
    wb.save(path)
    return read_cdr(path, cfg)


@pytest.fixture
def sced():
    gen = pd.DataFrame({
        "SCED Time Stamp": ["t1", "t2", "t1", "t1", "t1", "t1"],
        "Resource Name": ["ALPHA_UNIT1", "ALPHA_UNIT1", "ALPHA_UNIT2", "BRAVO_SLR", "GOLF_SOLAR1", "ZEPHYR_WIND1"],
        "Resource Type": ["PVGR", "PVGR", "PVGR", "PVGR", "PVGR", "WGR"],
        "QSE": ["Q1"] * 6,
        "HSL": [48.0, 50.5, 29.0, 149.0, 19.5, 180.0],
    })
    return summarize_sced_pv(gen)


@pytest.fixture
def mapping():
    return pd.DataFrame({"Resource Node": ["ALPHA_RN", "ALPHA_RN", "BRAVO_RN"],
                         "Unit Substation": ["ALPHA", "ALPHA", "BRAVO"],
                         "Unit Name": ["UNIT1", "UNIT2", "SLR"]})


@pytest.fixture
def plants(generators, polygons, cfg, as_of):
    return build_plants(generators, polygons, cfg, as_of)[0]


def per_plant(xw):
    return xw.groupby("eia_plant_id")


def test_read_cdr_finds_header_and_keeps_solar_only(cdr_raw):
    assert len(cdr_raw) == 6 and "ZEPHYR_WIND1" not in set(cdr_raw["UNIT CODE"])
    assert set(cdr_raw["sheet"]) == {"Existing Resources"}


def test_summarize_sced_pv(sced):
    s = sced.set_index("resource_name")
    assert list(s.index) == ["ALPHA_UNIT1", "ALPHA_UNIT2", "BRAVO_SLR", "GOLF_SOLAR1"]   # wind dropped
    assert s.loc["ALPHA_UNIT1"].max_hsl_mw == 50.5 and s.loc["ALPHA_UNIT1"].intervals == 2


def test_norm_name():
    stop = {"solar", "energy", "project", "llc"}
    assert norm_name("Taygete II Energy Project LLC", stop) == "taygete 2"
    assert norm_name("TAYGETE_SOLAR_UNIT2", stop) == "taygete unit2"


def test_matches(plants, cdr_raw, sced, mapping, cfg):
    xw = build(plants, cdr_raw, sced, mapping, cfg=cfg)
    assert list(xw.columns) == CSV_COLUMNS
    g = {k: v for k, v in per_plant(xw)}
    a = g[1]                                                     # two phases → two units, MW sums to 80
    assert sorted(a["ercot_resource_name"]) == ["ALPHA_UNIT1", "ALPHA_UNIT2"]
    assert set(a["match_confidence"]) == {"high"} and set(a["mw_error_pct"]) == {0.0}
    assert set(a["ercot_settlement_point"]) == {"ALPHA_RN"} and a["sced_coverage"].all()
    assert g[2].iloc[0].match_confidence == "high" and g[2].iloc[0].ercot_settlement_point == "BRAVO_RN"
    e = g[5].iloc[0]                                             # unnamed CDR row: MW + year + county
    assert e.ercot_resource_name == "ECHOX_UNIT1" and e.match_confidence == "medium"
    assert e.match_method == "auto:mw+year+county" and e.notes == "not in SCED PVGR list"
    f = g[6].iloc[0]
    assert f.match_confidence == "none" and pd.isna(f.ercot_resource_name)
    assert g[7].iloc[0].ercot_resource_name == "GOLF_SOLAR1"     # non-ERCOT BA still matched when in CDR
    h = g[8].iloc[0]                                             # right name, wrong county → low
    assert h.ercot_resource_name == "HOTEL_SOLAR1" and h.match_confidence == "low"
    assert h.match_method == "auto:name(no county match)"
    assert set(xw["eia_plant_id"]) == {1, 2, 5, 6, 7, 8}         # fail plants (3, 4) not matched


def test_unit_claimed_twice_is_downgraded(plants, cdr_raw, cfg):
    dup = cdr_raw.copy()
    dup.loc[dup["UNIT CODE"] == "BRAVO_SLR", "UNIT NAME"] = "Bravo Hotel Solar"
    p = plants.copy()
    p.loc[p["eia_id"] == 8, ["county", "plant_name"]] = ["Pecos", "Bravo Hotel"]
    xw = build(p, dup, cfg=cfg)
    b = xw[xw["ercot_resource_name"] == "BRAVO_SLR"]
    assert set(b["eia_plant_id"]) == {2, 8} and set(b["match_confidence"]) == {"low"}
    assert b["notes"].str.contains("claimed by EIA plants").all()


def test_verified_rows_are_never_overwritten(plants, cdr_raw, cfg):
    existing = pd.DataFrame([{
        "eia_plant_id": "2", "eia_plant_name": "Bravo", "ercot_resource_name": "MANUAL_BRAVO",
        "match_method": "manual", "match_confidence": "high", "verified_by": "CR", "verified_on": "2026-10-01",
    }])
    xw = build(plants, cdr_raw, existing=existing, cfg=cfg)
    b = xw[xw["eia_plant_id"].astype(str) == "2"]
    assert list(b["ercot_resource_name"]) == ["MANUAL_BRAVO"] and list(b["verified_by"]) == ["CR"]


def test_review_report(plants, cdr_raw, sced, mapping, cfg, tmp_path):
    xw = build(plants, cdr_raw, sced, mapping, cfg=cfg)
    text = review_report(xw, tmp_path / "r.md").read_text(encoding="utf-8-sig")
    assert "Plants: 6 · verified: 0 · high: 3 · medium: 1 · low: 1 · none: 1" in text
    assert text.index("| none |") < text.index("| low |") < text.index("| high |")   # worst first
