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


def _plants(rows):
    return pd.DataFrame([{"eia_id": i, "plant_name": n, "county": c, "ac_mw": mw,
                          "cod_first": pd.Timestamp(f"{y}-06-01"), "cod_last": pd.Timestamp(f"{y}-06-01"),
                          "filter_status": "pass"} for i, n, c, mw, y in rows])


def _cdr(rows):
    return pd.DataFrame([{"UNIT NAME": n, "UNIT CODE": code, "COUNTY": c, "FUEL": "SOLAR", "IN SERVICE YEAR": y,
                          "INSTALLED CAPACITY RATING": mw} for n, code, c, mw, y in rows])


def test_sibling_plants_split_units_by_mw_and_phase(cfg):
    """Real case from the first run: Prospero / Prospero II and Lamesa / Lamesa II each claimed every unit."""
    plants = _plants([(62755, "Prospero Solar", "Andrews", 300.0, 2020), (64325, "Prospero Solar II", "Andrews", 250.0, 2021),
                      (60372, "Lamesa Solar", "Dawson", 102.0, 2018), (61697, "Lamesa II", "Dawson", 50.0, 2018),
                      (61920, "Galloway 1 Solar Farm", "Concho", 250.0, 2021)])
    cdr = _cdr([("PROSPERO SOLAR 1 U1", "PROSPERO_UNIT1", "ANDREWS", 153.6, 2020),
                ("PROSPERO SOLAR 1 U2", "PROSPERO_UNIT2", "ANDREWS", 150.0, 2020),
                ("PROSPERO SOLAR 2 U1", "PRSPERO2_UNIT1", "ANDREWS", 126.5, 2021),
                ("PROSPERO SOLAR 2 U2", "PRSPERO2_UNIT2", "ANDREWS", 126.4, 2021),
                ("BNB LAMESA SOLAR (PHASE I)", "LMESASLR_UNIT1", "DAWSON", 101.6, 2018),
                ("BNB LAMESA SOLAR (PHASE II)", "LMESASLR_IVORY", "DAWSON", 50.0, 2018),
                ("GALLOWAY 1 SOLAR", "GALLOWAY_SOLAR1", "CONCHO", 250.0, 2021),
                ("GALLOWAY 2 SOLAR", "GALLOWAY_SOLAR2", "CONCHO", 111.1, 2024)])
    xw = build(plants, cdr, cfg=cfg)
    got = xw.groupby("eia_plant_id")["ercot_resource_name"].apply(sorted).to_dict()
    assert got[62755] == ["PROSPERO_UNIT1", "PROSPERO_UNIT2"]
    assert got[64325] == ["PRSPERO2_UNIT1", "PRSPERO2_UNIT2"]
    assert got[60372] == ["LMESASLR_UNIT1"] and got[61697] == ["LMESASLR_IVORY"]
    assert got[61920] == ["GALLOWAY_SOLAR1"]                      # 2024 Galloway 2 not pulled in
    assert set(xw["match_confidence"]) == {"high"}
    assert xw["ercot_resource_name"].is_unique


def test_two_eia_plants_sharing_one_unit(cfg):
    """Oberon IA (150) + IB (30) are one 180 MW ERCOT unit."""
    plants = _plants([(62933, "Oberon IA", "Ector", 150.0, 2020), (62932, "Oberon IB", "Ector", 30.0, 2020)])
    cdr = _cdr([("OBERON SOLAR", "OBERON_UNIT_1", "ECTOR", 180.0, 2020)])
    xw = build(plants, cdr, cfg=cfg)
    assert list(xw["ercot_resource_name"]) == ["OBERON_UNIT_1", "OBERON_UNIT_1"]
    assert set(xw["match_confidence"]) == {"medium"}
    assert xw["notes"].str.contains(r"shared by EIA plants \[62932, 62933\]").all()
    assert set(xw["mw_error_pct"]) == {0.0}


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
    assert "| Conf | Verified |" in text and "| high | — |" in text                  # nothing verified yet
    xw.loc[xw["eia_plant_id"] == 2, ["verified_by", "verified_on"]] = ["CR", "2026-10-02"]
    text2 = review_report(xw, tmp_path / "r2.md").read_text(encoding="utf-8-sig")
    assert "| high | CR 2026-10-02 | 2 | Bravo |" in text2


def test_verify_helper(plants, cdr_raw, sced, mapping, cfg):
    from pipeline.phase_3_market.verify import apply, is_verified

    xw = build(plants, cdr_raw, sced, mapping, cfg=cfg).astype(object)
    xw["eia_plant_id"] = xw["eia_plant_id"].astype(str)
    out = apply(xw, "CR", "2026-10-01", accept_high=True, sets=["8=HOTEL_SOLAR1"], no_resource={"6"},
                note=None)
    v = out[is_verified(out)]
    assert set(v["eia_plant_id"]) == {"1", "2", "7", "8", "6"}            # 3 high + manual + no-resource
    h = out[out["eia_plant_id"] == "8"].iloc[0]
    assert h.ercot_resource_name == "HOTEL_SOLAR1" and h.match_method == "manual" and h.verified_by == "CR"
    f = out[out["eia_plant_id"] == "6"].iloc[0]
    assert pd.isna(f.ercot_resource_name) and f.match_method == "manual" and f.verified_by == "CR"
    med = apply(xw, "CR", "2026-10-01", accept_medium=True)
    assert set(med.loc[is_verified(med), "match_confidence"]) == {"medium"}      # only the medium rows
    assert set(med.loc[is_verified(med), "eia_plant_id"]) == {"5"}                # ECHOX: mw+year+county fallback
    # verified rows survive a rebuild untouched
    again = build(plants, cdr_raw, sced, mapping, existing=out, cfg=cfg)
    assert again[again["eia_plant_id"].astype(str) == "8"]["ercot_resource_name"].tolist() == ["HOTEL_SOLAR1"]
    undone = apply(out, "CR", "2026-10-02", undo={"8"})
    assert not is_verified(undone[undone["eia_plant_id"] == "8"]).any()
