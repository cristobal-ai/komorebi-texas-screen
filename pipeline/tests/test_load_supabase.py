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
