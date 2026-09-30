import pandas as pd
import pytest

from pipeline.common import find_col, norm, read_excel_table, zip_member
from pipeline.phase_2_normalize.eia_annual import eia923_cf
from pipeline.phase_2_normalize.plant_master import build


def _read860(z):
    plant = read_excel_table((z, zip_member(z, "plant")), "Plant Code", ["Plant"])
    solar = read_excel_table((z, zip_member(z, "solar")), "Plant Code", ["Operable"])
    plant["data_year"] = solar["data_year"] = 2025
    return plant, solar


def _read923(z):
    g = read_excel_table((z, zip_member(z, "schedules_2_3_4_5")), "Plant Id", ["Page 1 Generation and Fuel Data"])
    g["data_year"] = 2025
    return g


def test_norm_and_find_col():
    df = pd.DataFrame(columns=["Plant Id", "Net Generation (Megawatthours)", "Grid Voltage 2 (kV)"])
    assert norm("Grid\nVoltage (kV)") == norm("grid_voltage_kv") == "gridvoltagekv"
    assert find_col(df, "Net Generation") == "Net Generation (Megawatthours)"    # prefix match
    assert find_col(df, "Grid Voltage (kV)") is None                             # no false prefix hit on "2"
    with pytest.raises(KeyError, match="available"):
        find_col(df, "Plant Code", required=True)


def test_readers_find_header_and_sheet(eia860_zip, eia923_zip):
    plant, solar = _read860(eia860_zip)
    assert list(plant["Plant Code"]) == [1, 2, 5, 8]
    assert "Nameplate Capacity (MW)" in solar.columns          # line break collapsed
    assert len(solar) == 5                                      # Operable only, not Retired
    g = _read923(eia923_zip)
    assert "Respondent Frequency" in g.columns and len(g) == 7


@pytest.fixture
def plants(generators, polygons, cfg, as_of, eia860_zip, eia923_zip):
    p, _ = build(generators, polygons, cfg, as_of, eia860=_read860(eia860_zip), gen923=_read923(eia923_zip))
    return p.set_index("eia_id")


def test_grid_voltage(plants):
    assert plants.loc[1].grid_voltage_kv == 345 and plants.loc[1].grid_voltage_source == "eia860"
    assert plants.loc[2].grid_voltage_kv == 138 and plants.loc[2].grid_voltage_max_kv == 345
    assert plants.loc[5].distribution_class_poi                 # 34.5 kV ≤ distribution_class_max_kv
    assert not plants.loc[1].distribution_class_poi
    assert pd.isna(plants.loc[8].grid_voltage_kv) and plants.loc[8].grid_voltage_source is None


def test_tracking_module_bifacial(plants):
    a = plants.loc[1]
    assert a.tracking_type == "single_axis" and a.tracking_type_share == 1.0
    assert a.module_tech == "c-Si"
    assert a.bifacial_share == pytest.approx(30 / 80)
    b = plants.loc[2]
    assert b.tracking_type == "fixed" and b.module_tech == "thin_film_CdTe"
    assert pd.isna(b.bifacial_share)                            # never answered
    e = plants.loc[5]
    assert e.tracking_type == "mixed" and e.tracking_type_share == pytest.approx(20 / 30)


def test_capacity_factor(plants):
    a = plants.loc[1]
    assert a.net_ac_cf == pytest.approx(0.28) and a.cf_series_resolution == "monthly" and a.cf_year == 2025
    assert a.net_mwh == pytest.approx(0.28 * 80 * 8760)         # storage row excluded
    b = plants.loc[2]
    assert b.net_ac_cf == pytest.approx(0.22) and b.cf_series_resolution == "annual"
    e = plants.loc[5]
    assert pd.isna(e.net_ac_cf) and e.cf_note == "incomplete_months"
    h = plants.loc[8]
    assert h.net_ac_cf == pytest.approx(0.45) and h.cf_note == "outside_plausible_range"
    g = plants.loc[7]
    assert pd.isna(g.net_ac_cf) and g.cf_note == "no_eia923_record"


def test_cf_partial_year_and_leap_year(cfg):
    plants = pd.DataFrame({"eia_id": [1, 2], "ac_mw": [100.0, 100.0],
                           "cod_last": [pd.Timestamp("2023-06-01"), pd.Timestamp("2024-03-01")]})
    gen = pd.DataFrame({"Plant Id": [1, 2], "Reported Prime Mover": ["PV", "PV"], "Respondent Frequency": ["A", "A"],
                        "Net Generation (Megawatthours)": [0.25 * 100 * 8784, 50000.0], "data_year": [2024, 2024]})
    out = eia923_cf(gen, plants, cfg).set_index("eia_id")
    assert out.loc[1].net_ac_cf == pytest.approx(0.25)          # 2024 has 8,784 h
    assert pd.isna(out.loc[2].net_ac_cf) and out.loc[2].cf_note == "partial_year_cod"


def test_build_without_annual_files_still_works(generators, polygons, cfg, as_of):
    p, _ = build(generators, polygons, cfg, as_of)
    assert "net_ac_cf" not in p.columns and len(p) == 8
