"""Phase 4 climate (NSRDB): CSV parsing, request points, wet-bulb, hour counts normalised to 8,760 h, summary, loader."""
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from pipeline.common import load_config
from pipeline.phase_4_geo import climate as cl
from pipeline.phase_4_geo import common as g
from pipeline.phase_4_geo import load as ld

CSV = """Source,Location ID,City,State,Country,Latitude,Longitude,Time Zone,Elevation,Local Time Zone,Temperature Units,Version
NSRDB,538838,-,-,-,30.89,-102.9,-6,915,-6,c,4.1.2
Year,Month,Day,Hour,Minute,Temperature,Relative Humidity,Dew Point
2024,1,1,0,30,4.4,53.07,-4.3
2024,1,1,1,30,26.5,30.0,7.0
"""


@pytest.fixture
def c():
    return load_config()["layers"]["climate"]


def test_parse_csv_reads_metadata_and_hours():
    df = cl.parse_csv(CSV)
    assert list(df["temp_c"]) == [4.4, 26.5] and list(df["rh_pct"]) == [53.07, 30.0]
    assert (df["location_id"] == 538838).all() and df["cell_lat"].iloc[0] == 30.89 and df["elevation_m"].iloc[0] == 915


def test_parse_csv_rejects_error_payload():
    with pytest.raises(ValueError):
        cl.parse_csv('{"errors":["The required \'email\' parameter must be a valid email address"]}')


def test_request_point_rounds_to_grid():
    assert cl.request_point(30.8912, -102.9071, 0.02) == (30.9, -102.9)
    assert cl.request_point(30.8790, -102.9301, 0.02) == (30.88, -102.94)


def test_plant_points_inside_polygon_and_point_fallback():
    df = pd.DataFrame({"eia_id": [1, 2], "filter_status": ["pass", "pass"], "lon": [-103.0, -100.5], "lat": [31.0, 30.5]})
    gdf = gpd.GeoDataFrame(df, geometry=[box(-103.01, 30.99, -102.99, 31.01), None], crs="EPSG:4326")
    pts = cl.plant_points(g.with_point_fallback(gdf)).set_index("eia_id")
    assert pts.loc[1, "lat"] == pytest.approx(31.0, abs=0.01) and pts.loc[2, "lon"] == pytest.approx(-100.5)


def test_wet_bulb_stull_reference_values():
    # Stull (2011) check value: 20 C, 50 % RH -> 13.7 C; saturated air: wet-bulb ~ dry-bulb
    assert cl.wet_bulb_stull(20.0, 50.0) == pytest.approx(13.7, abs=0.1)
    assert cl.wet_bulb_stull(25.0, 99.0) == pytest.approx(25.0, abs=0.3)


def hourly(year: int, n: int, temps, rh=40.0) -> pd.DataFrame:
    t = np.resize(np.asarray(temps, float), n)
    return pd.DataFrame({"year": year, "month": 1, "temp_c": t, "rh_pct": rh, "location_id": 7, "cell_lat": 31.0,
                         "cell_lon": -103.0, "elevation_m": 900.0})


def test_hours_normalised_to_8760_in_leap_year(c):
    # 3 of every 4 hours below 25 C -> 6,570 h whether the year has 8,760 or 8,784 rows
    a = cl.year_stats(hourly(2023, 8760, [10, 20, 24, 30]), c)
    b = cl.year_stats(hourly(2024, 8784, [10, 20, 24, 30]), c)
    assert a["below_25c_drybulb"] == pytest.approx(6570) and b["below_25c_drybulb"] == pytest.approx(6570)
    assert a["below_15c_drybulb"] == pytest.approx(2190) and a["above_35c_drybulb"] == 0


def test_summarize_means_years_and_keeps_worst(c):
    years = {2023: hourly(2023, 8760, [10, 20, 24, 30]), 2024: hourly(2024, 8784, [10, 30, 30, 30])}
    s = cl.summarize(years, hourly(2010, 8760, [10, 10, 10, 30]), c)
    assert s["hours_below_25c_drybulb"] == pytest.approx((6570 + 2190) / 2)
    assert s["hours_below_25c_drybulb_min"] == 2190 and s["hours_below_25c_drybulb_tmy"] == 6570
    assert s["hours_below_25c_by_year"] == "2023: 6570; 2024: 2190"
    assert s["climate_years"] == "2023-2024" and s["nsrdb_location_id"] == 7
    assert s["design_drybulb_0p4_c"] == pytest.approx(30.0) and s["max_drybulb_c"] == 30.0


def test_summary_keys_match_loader_columns(c):
    s = cl.summarize({2024: hourly(2024, 8784, [10, 30])}, None, c)
    extra = {"eia_id", "climate_source", "climate_fetched", "climate_confidence", "run_id"}
    assert set(s) | extra == set(ld.CLIMATE_COLUMNS)


class FakeResp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0

    def get(self, *a, **k):
        self.calls += 1
        return self.responses.pop(0)


def test_fetch_waits_out_rate_limit_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "raw_dir", lambda s: tmp_path / s)
    monkeypatch.setenv("NLR_API_KEY", "k")
    monkeypatch.setenv("NLR_API_EMAIL", "a@b.c")
    cfg = load_config()
    s = FakeSession([FakeResp(429, "OVER_RATE_LIMIT")] * 5 + [FakeResp(200, CSV)])   # more 429s than `retries`
    waits = []
    df = cl.fetch(30.9, -102.9, "ds", "2024", cfg, s, sleep=waits.append)
    assert len(df) == 2 and s.calls == 6 and waits[:2] == [10, 20]
    again = cl.fetch(30.9, -102.9, "ds", "2024", cfg, FakeSession([]), sleep=waits.append)   # cache hit, no request
    assert len(again) == 2


def test_fetch_bad_request_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "raw_dir", lambda s: tmp_path / s)
    monkeypatch.setenv("NLR_API_KEY", "k")
    monkeypatch.setenv("NLR_API_EMAIL", "a@b.c")
    s = FakeSession([FakeResp(400, '{"errors":["bad email"]}')])
    with pytest.raises(RuntimeError, match="HTTP 400"):
        cl.fetch(30.9, -102.9, "ds", "2024", load_config(), s, sleep=lambda x: None)
    assert s.calls == 1


def test_fetch_retries_transient_processing_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "raw_dir", lambda s: tmp_path / s)
    monkeypatch.setenv("NLR_API_KEY", "k")
    monkeypatch.setenv("NLR_API_EMAIL", "a@b.c")
    s = FakeSession([FakeResp(400, '{"status":400,"errors":["Data processing failure."]}'), FakeResp(200, CSV)])
    assert len(cl.fetch(30.9, -102.9, "ds", "2024", load_config(), s, sleep=lambda x: None)) == 2 and s.calls == 2


def test_loader_columns_match_migration():
    sql = next(Path("supabase/migrations").glob("*_layers_climate.sql")).read_text(encoding="utf-8")
    body = re.search(r"create table public\.layers_climate \((.*?)\n\);", sql, re.S).group(1)
    cols = [m.group(1) for line in body.splitlines() if (m := re.match(r"\s*([a-z_0-9]+)\s", line.split("--")[0]))]
    assert [c for c in cols if c != "loaded_at"] == ld.CLIMATE_COLUMNS
