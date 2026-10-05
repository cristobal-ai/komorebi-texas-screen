import re
from pathlib import Path

import pytest

from pipeline import load_supabase as ls
from pipeline.phase_2_normalize.plant_master import build

MIGRATION = next(Path("supabase/migrations").glob("*_plants.sql"))


def sql_columns() -> list[str]:
    body = re.search(r"create table public\.plants \((.*?)\n\);", MIGRATION.read_text(encoding="utf-8"), re.S).group(1)
    cols = []
    for line in body.splitlines():
        line = line.split("--")[0].strip()
        m = re.match(r"([a-z_0-9]+)\s", line)
        if m:
            cols.append(m.group(1))
    return cols


def test_metric_columns_match_migration():
    sql = next(Path("supabase/migrations").glob("*_plant_metrics.sql")).read_text(encoding="utf-8")
    added = re.findall(r"add column ([a-z_0-9]+)\s", sql)
    assert added == ls.METRIC_COLUMNS
    body = re.search(r"create table public\.plant_metrics_monthly \((.*?)\n\);", sql, re.S).group(1)
    monthly = [m.group(1) for line in body.splitlines() if (m := re.match(r"\s*([a-z_0-9]+)\s", line.split("--")[0]))
               and m.group(1) not in ("primary",)]
    assert monthly == ls.MONTHLY_COLUMNS


def test_loader_columns_match_migration():
    assert set(sql_columns()) - {"geom", "loaded_at"} == set(ls.PLANT_COLUMNS)


@pytest.fixture
def plants(generators, polygons, cfg, as_of):
    return build(generators, polygons, cfg, as_of)[0]


def test_rows_only_pass_and_review_and_json_safe(plants):
    rows = ls.to_rows(plants, "r1")
    by_id = {r["eia_id"]: r for r in rows}
    assert set(by_id) == {1, 2, 5, 6, 7, 8}                    # 3 and 4 fail
    a = by_id[1]
    assert a["cod_first"] == "2017-06-01" and a["run_id"] == "r1"
    assert isinstance(a["eia_id"], int) and isinstance(a["has_colocated_storage"], bool)
    assert a["geom"].startswith("SRID=4326;POLYGON")
    x, y = map(float, re.search(r"\(\(([-\d.]+) ([-\d.]+)", a["geom"]).groups())
    assert -110 < x < -90 and 20 < y < 40                        # reprojected to lon/lat, not Albers metres
    f = by_id[6]                                                 # no polygon, no DC
    assert f["geom"] is None and f["array_acres"] is None and f["dc_mw_source"] == "ilr_default"
    assert f["net_ac_cf"] is None                                # column absent without EIA-923 → null
    import json
    json.dumps(rows)                                             # no NaN / numpy / Timestamp left


class FakeResp:
    def __init__(self, ok=True, status=200, headers=None, text=""):
        self.ok, self.status_code, self.headers, self.text = ok, status, headers or {}, text


class FakeSession:
    def __init__(self, count):
        self.calls, self.count = [], count

    def post(self, url, json, headers, timeout):
        self.calls.append(("POST", url, len(json), headers))
        return FakeResp()

    def delete(self, url, headers, timeout):
        self.calls.append(("DELETE", url, 0, headers))
        return FakeResp()

    def get(self, url, headers, timeout):
        self.calls.append(("GET", url, 0, headers))
        return FakeResp(headers={"Content-Range": f"0-0/{self.count}"})


def test_load_upserts_deletes_stale_and_verifies(plants, monkeypatch):
    monkeypatch.setattr(ls, "BATCH", 4)
    s = FakeSession(count=6)
    assert ls.load(plants, "https://x.supabase.co/", "sb_secret_abc", run_id="r9", session=s) == 6
    kinds = [c[0] for c in s.calls]
    assert kinds == ["POST", "POST", "DELETE", "GET"]            # 6 rows in batches of 4
    assert s.calls[0][1].endswith("/rest/v1/plants?on_conflict=eia_id")
    assert "merge-duplicates" in s.calls[0][3]["Prefer"]
    assert s.calls[2][1].endswith("run_id=neq.r9")
    assert "Authorization" not in s.calls[0][3]                  # sb_ key: apikey header only


def test_load_fails_loudly_on_count_mismatch(plants):
    with pytest.raises(RuntimeError, match="row count mismatch"):
        ls.load(plants, "https://x.supabase.co", "sb_secret_abc", run_id="r9", session=FakeSession(count=5))


def test_legacy_jwt_gets_bearer():
    assert ls.headers("eyJhbGciOi...")["Authorization"].startswith("Bearer ")


def test_metrics_merge_into_rows_only_when_present(plants):
    import pandas as pd

    plain = ls.to_rows(plants, "r1")
    assert "capture_rate" not in plain[0]                         # no 3b file → columns not sent, nothing nulled
    m = pd.DataFrame({"eia_id": [1], "resources": ["U1+U2"], "settlement_points": ["N1"], "capture_rate": [0.61],
                      "metrics_window_months": [30], "window_start": ["2023-12"], "ercot_resource_shared": [False]})
    rows = {r["eia_id"]: r for r in ls.to_rows(plants, "r1", m)}
    assert rows[1]["capture_rate"] == 0.61 and rows[1]["ercot_resources"] == "U1+U2"
    assert rows[1]["metrics_window_months"] == 30 and rows[1]["metrics_window_start"] == "2023-12"
    assert rows[2]["capture_rate"] is None and rows[2]["metrics_status"] is None
    import json
    json.dumps(list(rows.values()))


def test_monthly_rows_json_safe_and_filtered():
    import json

    import pandas as pd

    mo = pd.DataFrame({c: [1.0, 1.0] for c in ls.MONTHLY_COLUMNS if c not in ("run_id",)})
    mo["eia_id"], mo["month"], mo["n_units"], mo["days"] = [1, 9], "2026-07", [2, 1], [31, 31]
    mo["capture_rate"] = [float("nan"), 0.5]
    rows = ls.monthly_rows(mo, {1}, "r1")
    assert len(rows) == 1 and rows[0]["capture_rate"] is None and rows[0]["n_units"] == 2 and rows[0]["gen_mwh"] == 1.0
    json.dumps(rows)


def test_send_retries_connection_resets_and_5xx(monkeypatch):
    import requests

    monkeypatch.setattr(ls.time, "sleep", lambda s: None)
    calls = []

    class Flaky:
        def post(self, url, **kw):
            calls.append(url)
            if len(calls) == 1:
                raise requests.exceptions.ConnectionError("reset")
            if len(calls) == 2:
                return FakeResp(ok=False, status=503)
            return FakeResp()

    r = ls._send(Flaky(), "post", "https://x/rest/v1/plants", json=[], timeout=1, headers={})
    assert r.ok and len(calls) == 3

    class Dead:
        def post(self, url, **kw):
            raise requests.exceptions.ConnectionError("down")

    with pytest.raises(requests.exceptions.ConnectionError):
        ls._send(Dead(), "post", "https://x/rest/v1/plants", retries=2, json=[], timeout=1, headers={})
