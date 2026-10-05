"""Phase 3b: curtailment and capture-rate arithmetic on a hand-computable synthetic day."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from pipeline.phase_1_ingest import ercot_history
from pipeline.phase_3_market import market, metrics


@pytest.fixture
def cfg(cfg):
    return cfg


def sced_rows(resource="SOL_UNIT1", rtype="PVGR", day="2026-07-15", n=12, hsl=100.0, bp=80.0, tno=80.0, start_hour=12):
    """n SCED runs five minutes apart from start_hour local."""
    ts = pd.date_range(f"{day} {start_hour:02d}:00:00", periods=n, freq="5min")
    return pd.DataFrame({
        "SCED Time Stamp": ts.strftime("%m/%d/%Y %H:%M:%S"), "Repeated Hour Flag": "N",
        "Resource Name": resource, "Resource Type": rtype,
        "Telemetered Net Output": tno, "Base Point": bp, "HSL": hsl,
    })


def test_sced_to_15min_curtailment_and_energy(cfg):
    g = metrics.sced_to_15min(sced_rows(), cfg)
    assert len(g) == 4                                            # 12 runs × 5 min = 1 h = 4 settlement intervals
    assert g["hours"].sum() == pytest.approx(1.0)
    assert g["gen_mwh"].sum() == pytest.approx(80.0)              # 80 MW for 1 h
    assert g["hsl_mwh"].sum() == pytest.approx(100.0)
    assert g["curtailed_mwh"].sum() == pytest.approx(20.0)        # HSL − Base Point = 20 MW for 1 h
    assert str(g["interval_start"].dt.tz) == "UTC"
    assert g["interval_start"].iloc[0] == pd.Timestamp("2026-07-15 17:00", tz="UTC")   # 12:00 CDT


def test_non_pv_resources_ignored_and_no_negative_curtailment(cfg):
    rows = pd.concat([sced_rows(rtype="WIND"), sced_rows(resource="B", bp=120.0, tno=100.0)])
    g = metrics.sced_to_15min(rows, cfg)
    assert set(g["resource_name"]) == {"B"}
    assert g["curtailed_mwh"].sum() == 0                          # Base Point above HSL is not negative curtailment


def test_gap_longer_than_max_interval_uses_default_length(cfg):
    r = sced_rows(n=2)
    r.loc[1, "SCED Time Stamp"] = "07/15/2026 14:00:00"           # two-hour gap
    g = metrics.sced_to_15min(r, cfg)
    assert g["hours"].sum() == pytest.approx(2 * 5 / 60)


def test_repeated_hour_flag_resolves_fall_back(cfg):
    a = sced_rows(day="2026-11-01", start_hour=1, n=1)            # 01:00 first pass (CDT)
    b = sced_rows(day="2026-11-01", start_hour=1, n=1).assign(**{"Repeated Hour Flag": "Y"})
    g = metrics.sced_to_15min(pd.concat([a, b]), cfg)
    assert len(g) == 2
    assert (g["interval_start"].iloc[1] - g["interval_start"].iloc[0]) == pd.Timedelta(hours=1)


def spp_frame(day="2026-07-15"):
    idx = pd.date_range(f"{day} 17:00", periods=4, freq="15min", tz="UTC")
    rows = []
    for loc, prices in (("HB_HUBAVG", [50, 50, 50, 50]), ("NODE_A", [25, 25, 25, 25]), ("NODE_B", [40, 40, 40, 40])):
        rows += [{"interval_start": t, "location": loc, "spp": p} for t, p in zip(idx, prices)]
    return pd.DataFrame(rows)


def xw(rows):
    return pd.DataFrame(rows, columns=["eia_plant_id", "ercot_resource_name", "ercot_settlement_point", "ac_mw_eia"])


def test_capture_rate_hub_and_basis(cfg):
    sced = metrics.sced_to_15min(sced_rows(), cfg)
    x = xw([(1, "SOL_UNIT1", "NODE_A", 100.0)])
    units = metrics.unit_monthly(sced, spp_frame(), x, cfg)
    m = metrics.plant_monthly(units, pd.Series({1: 100.0}), cfg)
    r = m.iloc[0]
    assert r["month"] == "2026-07"
    assert r["capture_rate"] == pytest.approx(25 / 50)
    assert r["shape_capture"] == pytest.approx(1.0)               # flat hub price → no shape discount
    assert r["basis_ratio"] == pytest.approx(0.5)
    assert r["curtailment_pct"] == pytest.approx(0.2)
    assert r["sced_net_cf"] == pytest.approx(0.8)                 # 80 MWh / (100 MW × 1 h)


def test_two_units_sum_to_plant_and_weight_prices(cfg):
    sced = pd.concat([metrics.sced_to_15min(sced_rows("U1"), cfg), metrics.sced_to_15min(sced_rows("U2", tno=40.0, bp=40.0), cfg)])
    x = xw([(1, "U1", "NODE_A", 100.0), (1, "U2", "NODE_B", 100.0)])
    m = metrics.plant_monthly(metrics.unit_monthly(sced, spp_frame(), x, cfg), pd.Series({1: 100.0}), cfg).iloc[0]
    assert m["gen_mwh"] == pytest.approx(120.0)
    assert m["node_gen_wtd_spp"] == pytest.approx((80 * 25 + 40 * 40) / 120)
    assert m["n_units"] == 2


def test_summary_statuses_and_thresholds(cfg):
    sced = metrics.sced_to_15min(sced_rows(), cfg)
    x = xw([(1, "SOL_UNIT1", "NODE_A", 100.0), (2, None, None, 50.0), (3, "MISSING_UNIT", "NODE_A", 50.0),
            (4, "SOL_UNIT1", None, 100.0)])
    ac = x.groupby("eia_plant_id")["ac_mw_eia"].first()
    cfg = {**cfg, "market_metrics": {**cfg["market_metrics"], "min_month_coverage": 0.0, "min_gen_mwh_for_capture": 10}}
    units = metrics.unit_monthly(sced, spp_frame(), x, cfg)
    s = metrics.plant_summary(metrics.plant_monthly(units, ac, cfg), x, ac, cfg).set_index("eia_id")
    assert s.loc[1, "metrics_status"] == "ok" and s.loc[1, "capture_rate"] == pytest.approx(0.5)
    assert s.loc[2, "metrics_status"] == "no_ercot_resource" and pd.isna(s.loc[2, "capture_rate"])
    assert s.loc[2, "sced_coverage"] == "none" and s.loc[2, "metrics_window_months"] == 0
    assert s.loc[3, "metrics_status"] == "no_sced_data"
    assert s.loc[4, "metrics_status"] == "no_settlement_point"
    assert s.loc[4, "curtailment_pct"] == pytest.approx(0.2)      # curtailment still reported without a price node
    assert bool(s.loc[1, "ercot_resource_shared"]) and bool(s.loc[4, "ercot_resource_shared"])   # unit used by plants 1 and 4


def test_short_month_excluded_from_summary(cfg):
    sced = metrics.sced_to_15min(sced_rows(), cfg)                # one day of July
    x = xw([(1, "SOL_UNIT1", "NODE_A", 100.0)])
    ac = pd.Series({1: 100.0})
    units = metrics.unit_monthly(sced, spp_frame(), x, cfg)
    s = metrics.plant_summary(metrics.plant_monthly(units, ac, cfg), x, ac, cfg).iloc[0]
    assert s["metrics_window_months"] == 0 and s["metrics_status"] == "no_full_months"


def test_window_respects_floor_and_lag(cfg):
    start, end = ercot_history.window(cfg, today=dt.date(2026, 10, 3))
    assert end == dt.date(2026, 10, 3) - dt.timedelta(days=62)
    assert start == dt.date(2023, 12, 11) or start > dt.date(2023, 12, 11)
    assert ercot_history.window(cfg, since=dt.date(2026, 7, 1), until=dt.date(2026, 7, 2))[0] == dt.date(2026, 7, 1)


def test_report_writes(tmp_path, cfg):
    sced = metrics.sced_to_15min(sced_rows(), cfg)
    x = xw([(1, "SOL_UNIT1", "NODE_A", 100.0), (2, None, None, 50.0)])
    cfg2 = {**cfg, "market_metrics": {**cfg["market_metrics"], "min_month_coverage": 0.0, "min_gen_mwh_for_capture": 10}}
    monthly, summary = market.compute(sced, spp_frame(), x, cfg2)
    p = market.report(summary, None, cfg2, (dt.date(2026, 7, 15), dt.date(2026, 7, 15)), tmp_path / "r.md")
    text = p.read_text(encoding="utf-8-sig")
    assert "no_ercot_resource" in text and "capture_rate" in text


class FakeAPI:
    def __init__(self):
        self.sced_calls = self.spp_calls = 0

    def get_60_day_sced_disclosure(self, date, process=False):
        self.sced_calls += 1
        return {"sced_gen_resource": sced_rows(day=date.strftime("%Y-%m-%d"))}

    def get_spp_real_time_15_min(self, date, end=None):
        self.spp_calls += 1
        f = spp_frame(date.strftime("%Y-%m-%d"))
        return pd.DataFrame({"Interval Start": f["interval_start"], "Location": f["location"], "SPP": f["spp"]})


def test_ingest_is_resumable_and_filters_locations(tmp_path, monkeypatch, cfg):
    api = FakeAPI()
    monkeypatch.setattr(ercot_history, "_api", lambda: api)
    monkeypatch.setattr(ercot_history, "raw_dir", lambda s: tmp_path / s)
    (tmp_path / "ercot").mkdir()
    monkeypatch.setattr(ercot_history.time, "sleep", lambda s: None)
    x = pd.DataFrame({"ercot_settlement_point": ["NODE_A"]})
    a, b = dt.date(2026, 7, 14), dt.date(2026, 7, 15)
    r = ercot_history.run(a, b, x)
    assert r["fetched"] == 2 and api.sced_calls == 2 and api.spp_calls == 2
    ercot_history.run(a, b, x)                                    # second run hits the cache only
    assert api.sced_calls == 2 and api.spp_calls == 2
    sced, spp = ercot_history.load_cached(a, b)
    assert set(spp["location"]) == {"HB_HUBAVG", "NODE_A"}        # NODE_B not in the crosswalk → dropped at ingest
    assert sced["gen_mwh"].sum() == pytest.approx(160.0)


def test_tz_aware_sced_timestamps_accepted(cfg):
    r = sced_rows()
    r["SCED Time Stamp"] = pd.to_datetime(r["SCED Time Stamp"]).dt.tz_localize("US/Central")
    g = metrics.sced_to_15min(r, cfg)
    assert g["gen_mwh"].sum() == pytest.approx(80.0)
    assert g["interval_start"].iloc[0] == pd.Timestamp("2026-07-15 17:00", tz="UTC")


def test_new_settlement_point_invalidates_cached_prices(tmp_path, monkeypatch, cfg):
    api = FakeAPI()
    monkeypatch.setattr(ercot_history, "_api", lambda: api)
    monkeypatch.setattr(ercot_history, "raw_dir", lambda s: tmp_path / s)
    (tmp_path / "ercot").mkdir()
    monkeypatch.setattr(ercot_history.time, "sleep", lambda s: None)
    day = dt.date(2026, 7, 15)
    ercot_history.run(day, day, pd.DataFrame({"ercot_settlement_point": ["NODE_A"]}))
    ercot_history.run(day, day, pd.DataFrame({"ercot_settlement_point": ["NODE_A"]}))
    assert api.spp_calls == 1 and api.sced_calls == 1             # nothing new → cache hit
    ercot_history.run(day, day, pd.DataFrame({"ercot_settlement_point": ["NODE_A", "NODE_B"]}))
    assert api.spp_calls == 2 and api.sced_calls == 1             # prices refetched, SCED kept
    _, spp = ercot_history.load_cached(day, day)
    assert "NODE_B" in set(spp["location"])


def test_peak_hsl_ratio_flags_derated_plant(cfg):
    sced = metrics.sced_to_15min(sced_rows(hsl=25.0, bp=25.0, tno=25.0), cfg)     # 25 MW capability on a 100 MW plant
    x = xw([(1, "SOL_UNIT1", "NODE_A", 100.0)])
    ac = pd.Series({1: 100.0})
    cfg2 = {**cfg, "market_metrics": {**cfg["market_metrics"], "min_month_coverage": 0.0, "min_gen_mwh_for_capture": 1}}
    s = metrics.plant_summary(metrics.plant_monthly(metrics.unit_monthly(sced, spp_frame(), x, cfg2), ac, cfg2), x, ac, cfg2).iloc[0]
    assert s["peak_hsl_mw"] == pytest.approx(25.0, rel=0.02)
    assert s["peak_hsl_ratio"] == pytest.approx(0.25, rel=0.02)


def test_missing_archive_day_is_marked_not_retried(tmp_path, monkeypatch, cfg):
    class GapAPI(FakeAPI):
        def get_60_day_sced_disclosure(self, date, process=False):
            if date.day == 14:
                self.sced_calls += 1
                raise KeyError("archives")                        # what gridstatus raises for an unposted day
            return super().get_60_day_sced_disclosure(date, process)

    api = GapAPI()
    monkeypatch.setattr(ercot_history, "_api", lambda: api)
    monkeypatch.setattr(ercot_history, "raw_dir", lambda s: tmp_path / s)
    (tmp_path / "ercot").mkdir()
    monkeypatch.setattr(ercot_history.time, "sleep", lambda s: None)
    x = pd.DataFrame({"ercot_settlement_point": ["NODE_A"]})
    a, b = dt.date(2026, 7, 14), dt.date(2026, 7, 15)
    r = ercot_history.run(a, b, x)
    assert r["gaps"] == [a] and not r["failed"] and api.sced_calls == 2      # no retries on a gap
    ercot_history.run(a, b, x)
    assert api.sced_calls == 2                                                # marker honoured
    ercot_history.run(a, b, x, retry_missing=True)
    assert api.sced_calls == 3                                                # explicit retry only


def test_interrupted_write_is_detected_and_refetched(tmp_path, monkeypatch, cfg):
    api = FakeAPI()
    monkeypatch.setattr(ercot_history, "_api", lambda: api)
    monkeypatch.setattr(ercot_history, "raw_dir", lambda s: tmp_path / s)
    (tmp_path / "ercot").mkdir()
    monkeypatch.setattr(ercot_history.time, "sleep", lambda s: None)
    x = pd.DataFrame({"ercot_settlement_point": ["NODE_A"]})
    day = dt.date(2026, 7, 15)
    ercot_history.run(day, day, x)
    f = tmp_path / "ercot" / "sced_15min" / f"{day}.parquet"
    f.write_bytes(f.read_bytes()[:200])                           # simulate a power cut mid-write
    (tmp_path / "ercot" / "spp_15min" / "2026-07-14.parquet.part").write_bytes(b"junk")
    ercot_history.run(day, day, x)
    assert api.sced_calls == 2 and api.spp_calls == 1             # SCED refetched, prices kept
    assert not list((tmp_path / "ercot" / "spp_15min").glob("*.part"))
    sced, _ = ercot_history.load_cached(day, day)
    assert sced["gen_mwh"].sum() == pytest.approx(80.0)


def test_day_with_missing_load_file_falls_back_to_gen_file(tmp_path, monkeypatch, cfg):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("60d_SCED_Gen_Resource_Data-15-JUL-26.csv", sced_rows(day="2026-07-15").to_csv(index=False))
    seen = {}

    class ZipGapAPI(FakeAPI):
        def get_60_day_sced_disclosure(self, date, process=False):
            raise AssertionError("Could not find load resource file")

        def get_historical_data(self, endpoint, start_date, end_date, read_as_csv=True):
            seen["start"], seen["csv"] = start_date, read_as_csv
            return [buf.getvalue()]

    out = ercot_history.fetch_sced_day(ZipGapAPI(), dt.date(2026, 7, 15), cfg)
    assert out["gen_mwh"].sum() == pytest.approx(80.0)
    assert seen["start"] == pd.Timestamp("2026-09-13") and seen["csv"] is False   # posted 60 days after the operating day


def test_potential_capture_not_flattered_by_curtailment(cfg):
    """A plant curtailed in the cheap hours sells at a high price; potential capture prices what it could have sold."""
    ts = pd.date_range("2026-07-15 12:00:00", periods=24, freq="5min")            # 2 h = 8 settlement intervals
    cheap = np.arange(24) >= 12                                                 # second hour curtailed to zero
    g = pd.DataFrame({
        "SCED Time Stamp": ts.strftime("%m/%d/%Y %H:%M:%S"), "Repeated Hour Flag": "N", "Resource Name": "U", "Resource Type": "PVGR",
        "HSL": 100.0, "Base Point": np.where(cheap, 0.0, 100.0), "Telemetered Net Output": np.where(cheap, 0.0, 100.0)})
    sced = metrics.sced_to_15min(g, cfg)
    idx = pd.date_range("2026-07-15 17:00", periods=8, freq="15min", tz="UTC")
    rows = []
    for loc, prices in (("HUB", [50] * 8), ("N", [60] * 4 + [-20] * 4)):
        rows += [{"interval_start": t, "location": loc, "spp": p} for t, p in zip(idx, prices)]
    cfg2 = {**cfg, "market_metrics": {**cfg["market_metrics"], "hub_reference": "HUB", "min_month_coverage": 0.0,
                                       "min_gen_mwh_for_capture": 1}}
    x = xw([(1, "U", "N", 100.0)])
    ac = pd.Series({1: 100.0})
    s = metrics.plant_summary(metrics.plant_monthly(metrics.unit_monthly(sced, pd.DataFrame(rows), x, cfg2), ac, cfg2), x, ac, cfg2).iloc[0]
    assert s["capture_rate"] == pytest.approx(60 / 50)                          # delivered only in the 60 $/MWh hour
    assert s["capture_rate_potential"] == pytest.approx(20 / 50)                # average of 60 and -20 over HSL
    assert s["peak_hsl_ratio_recent"] == pytest.approx(s["peak_hsl_ratio"])


def test_unit_history_shows_when_units_started(tmp_path, monkeypatch, cfg):
    monkeypatch.setattr(ercot_history, "raw_dir", lambda s: tmp_path / s)
    d = tmp_path / "ercot" / "sced_15min"
    d.mkdir(parents=True)
    for day, hsl in (("2026-05-10", 0.0), ("2026-06-10", 50.0)):
        g = sced_rows(resource="NEW_U1", day=day, hsl=hsl, bp=hsl, tno=hsl)
        metrics.sced_to_15min(g, cfg).to_parquet(d / f"{day}.parquet")
    monkeypatch.setattr(ercot_history, "window", lambda cfg: (dt.date(2026, 5, 1), dt.date(2026, 6, 30)))
    out = ercot_history.history(["NEW_U1", "OTHER"])
    assert "2026-05" in out and "2026-06" in out and "50.0" in out and "NEW_U1" in out
