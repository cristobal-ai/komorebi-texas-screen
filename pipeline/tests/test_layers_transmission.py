"""Phase 4 transmission layer: voltage parsing, Overpass parsing, distances in miles, POI cross-check."""
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, box

from pipeline.phase_4_geo import common as g
from pipeline.phase_4_geo import transmission as t


def test_parse_kv_handles_osm_and_hifld_forms():
    assert t.parse_kv("345000") == 345
    assert t.parse_kv("345000;138000") == 345
    assert t.parse_kv("138 kV") == 138
    assert t.parse_kv("138") == 138
    assert t.parse_kv("69000") == 69
    assert np.isnan(t.parse_kv("high")) and np.isnan(t.parse_kv(None)) and np.isnan(t.parse_kv("0"))


def test_hifld_kv_prefers_numeric_then_class():
    assert t.hifld_kv({"voltage": 345.0, "volt_class": "345"}) == 345
    assert t.hifld_kv({"voltage": -999999, "volt_class": "220-287"}) == 230
    assert t.hifld_kv({"voltage": -999999, "volt_class": "100-161"}) == 138
    assert np.isnan(t.hifld_kv({"voltage": -999999, "volt_class": ""}))


def test_overpass_parsing_lines_and_substations():
    lines = t.parse_overpass({"elements": [
        {"type": "way", "id": 1, "tags": {"voltage": "345000"}, "geometry": [{"lat": 31.0, "lon": -103.0}, {"lat": 31.1, "lon": -103.0}]},
        {"type": "way", "id": 2, "tags": {"voltage": "unknown"}, "geometry": [{"lat": 31.0, "lon": -103.0}, {"lat": 31.1, "lon": -103.0}]},
        {"type": "way", "id": 3, "tags": {"voltage": "138000"}, "geometry": [{"lat": 31.0, "lon": -103.0}]},      # one point
    ]}, "line")
    assert list(lines["osm_id"]) == ["w1"] and lines.iloc[0]["voltage_kv"] == 345
    subs = t.parse_overpass({"elements": [
        {"type": "node", "id": 5, "lat": 31.0, "lon": -103.0, "tags": {"voltage": "345000;138000", "name": "Solstice"}},
        {"type": "way", "id": 6, "center": {"lat": 31.2, "lon": -103.1}, "tags": {"voltage": "138000"}},
        {"type": "relation", "id": 7, "tags": {"voltage": "345000"}},                                             # no centre
    ]}, "substation")
    assert set(subs["osm_id"]) == {"n5", "w6"} and subs.set_index("osm_id").loc["n5", "name"] == "Solstice"
    assert len(t.parse_overpass({"elements": []}, "line")) == 0


@pytest.fixture
def plants():
    # two plants ~0.1 deg lat apart near Pecos (1 deg lat ≈ 69 mi), third has no polygon (point fallback)
    poly = lambda lon, lat: box(lon - 0.002, lat - 0.002, lon + 0.002, lat + 0.002)
    df = pd.DataFrame({
        "eia_id": [1, 2, 3], "filter_status": ["pass", "pass", "review"],
        "lon": [-103.0, -103.0, -102.0], "lat": [31.0, 31.5, 31.0], "grid_voltage_kv": [345.0, 138.0, np.nan],
    })
    geoms = [poly(-103.0, 31.0), poly(-103.0, 31.5), None]
    return g.with_point_fallback(gpd.GeoDataFrame(df, geometry=geoms, crs="EPSG:4326"))


def test_point_fallback_marks_plants_without_polygon(plants):
    assert plants["geometry_is_point"].tolist() == [False, False, True]
    assert plants.geometry.iloc[2].geom_type == "Point"


def _layer(rows, kind):
    gdf = gpd.GeoDataFrame([{"osm_id": r[0], "name": r[1], "operator": None, "voltage_kv": r[2]} for r in rows],
                           geometry=[r[3] for r in rows], crs="EPSG:4326")
    return gdf


def test_distances_in_miles_and_poi_crosscheck(plants, cfg):
    lines = _layer([
        ("w1", None, 345, LineString([(-103.05, 31.0), (-103.05, 31.2)])),      # 0.05 deg lon west of plant 1 ≈ 3.0 mi
        ("w2", None, 138, LineString([(-103.0, 31.4), (-103.0, 31.45)])),       # ~0.05 deg lat south of plant 2 ≈ 3.4 mi
    ], "line")
    subs = _layer([("n1", "Solstice", 345, Point(-103.0, 31.1)), ("n2", "Small", 138, Point(-103.0, 31.5))], "sub")
    out = t.compute(plants, lines, subs, cfg, "osm", "2026-10-04").set_index("eia_id")

    assert out.loc[1, "dist_345kv_sub_mi"] == pytest.approx(0.1 * 69.0, rel=0.03)      # 0.1 deg lat
    assert out.loc[1, "nearest_345kv_sub_name"] == "Solstice"
    assert out.loc[1, "dist_345kv_line_mi"] == pytest.approx(0.05 * 59.0, rel=0.05)    # 0.05 deg lon at 31 N ≈ 3 mi
    assert out.loc[2, "dist_345kv_sub_mi"] > 25                                        # nearest 345 sub is 0.4 deg away
    assert out.loc[2, "dist_138kv_line_mi"] == pytest.approx(0.05 * 69.0, rel=0.1, abs=0.5)
    # POI cross-check within 3 miles: plant 1's 345 kV line is ~2.9 mi away (seen); plant 2's 138 kV line ~3.3 mi (not seen)
    assert out.loc[1, "kv_classes_within_near"] == "345" and out.loc[1, "poi_kv_seen"] is True
    assert out.loc[2, "kv_classes_within_near"] is None and out.loc[2, "poi_kv_seen"] is False
    assert out.loc[1, "max_kv_within_near"] == 345 and pd.isna(out.loc[2, "max_kv_within_near"])
    assert out.loc[1, "transmission_source"] == "osm" and out.loc[1, "transmission_confidence"] == "medium"
    assert out.loc[3, "transmission_confidence"] == "low"                              # point only
    assert pd.isna(out.loc[3, "poi_kv_seen"])                                         # no EIA POI voltage


def test_empty_layers_give_nan_not_zero(plants, cfg):
    empty = _layer([], "line")
    out = t.compute(plants, empty, empty, cfg, "osm", "2026-10-04")
    assert out["dist_345kv_sub_mi"].isna().all() and out["dist_345kv_line_mi"].isna().all()
    assert out["kv_classes_within_near"].isna().all()


def test_loader_columns_match_migration_and_rows_are_json_safe(plants, cfg):
    import json
    import re
    from pathlib import Path

    from pipeline.phase_4_geo import load as ld

    sql = next(Path("supabase/migrations").glob("*_layers_transmission.sql")).read_text(encoding="utf-8")
    body = re.search(r"create table public\.layers_transmission \((.*?)\n\);", sql, re.S).group(1)
    cols = [m.group(1) for line in body.splitlines() if (m := re.match(r"\s*([a-z_0-9]+)\s", line.split("--")[0]))]
    assert [c for c in cols if c != "loaded_at"] == ld.TRANSMISSION_COLUMNS

    lines = _layer([("w1", None, 345, LineString([(-103.05, 31.0), (-103.05, 31.2)]))], "line")
    df = t.compute(plants, lines, _layer([], "sub"), cfg, "osm", "2026-10-04")
    rows = ld.to_rows(df, ld.TRANSMISSION_COLUMNS, "r1")
    json.dumps(rows)
    by = {r["eia_id"]: r for r in rows}
    assert by[3]["dist_345kv_sub_mi"] is None and by[1]["poi_kv_seen"] is True and by[1]["run_id"] == "r1"


def test_run_py_phase4_is_explicit_not_part_of_all():
    from pipeline import run as r

    assert r._phases("all") == [1, 2, 3] and r._phases("4") == [4]


def test_plant_bboxes_cover_every_plant_and_share_boxes_in_one_grid_cell(plants, cfg):
    boxes = t.plant_bboxes(plants, cfg)
    w = 2 * cfg["layers"]["transmission"]["bbox_half_deg"]
    assert len(boxes) == 3                                   # plants sit in three different snap-grid cells
    assert all(b[2] - b[0] == pytest.approx(w) and b[3] - b[1] == pytest.approx(w) for b in boxes)
    for lat, lon in zip(plants["lat"], plants["lon"]):
        assert any(b[0] <= lat <= b[2] and b[1] <= lon <= b[3] for b in boxes)
    near = plants.copy()
    near["lat"], near["lon"] = [31.05, 31.1, 31.0], [-103.1, -103.05, -102.95]      # all within the snap-grid cell at (31, -103)
    assert len(t.plant_bboxes(near, cfg)) == 1


class FakeOverpass:
    def __init__(self):
        self.queries = []

    def post(self, url, data, timeout, headers):
        q = data["data"]
        self.queries.append(q)

        class R:
            def raise_for_status(self):
                pass

            def json(self_inner):
                if "power\"=\"line" in q:
                    return {"elements": [{"type": "way", "id": 11, "tags": {"voltage": "345000"},
                                          "geometry": [{"lat": 31.0, "lon": -103.05}, {"lat": 31.2, "lon": -103.05}]}]}
                return {"elements": [{"type": "node", "id": 12, "lat": 31.1, "lon": -103.0,
                                      "tags": {"voltage": "345000", "name": "Solstice"}}]}
        return R()


def test_fetch_osm_uses_boxes_caches_them_and_dedupes(plants, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(t, "raw_dir", lambda s: tmp_path / s)
    (tmp_path / "transmission").mkdir()
    s = FakeOverpass()
    lines, subs, fetched = t.fetch_osm(plants, cfg, s, sleep=lambda x: None)
    n = len(t.plant_bboxes(plants, cfg))
    assert len(s.queries) == 2 * n and n == 3
    assert len(lines) == 1 and len(subs) == 1                # the same way/node came back from every box → one row
    t.fetch_osm(plants, cfg, s, sleep=lambda x: None)
    assert len(s.queries) == 2 * n                           # second run is all cache


def test_overpass_retries_other_mirror_then_raises(cfg):
    class Bad:
        calls = 0

        def post(self, url, **kw):
            Bad.calls += 1
            raise RuntimeError("504")

    with pytest.raises(RuntimeError, match="all Overpass endpoints failed"):
        t._overpass("q", cfg, Bad(), sleep=lambda x: None)
    assert Bad.calls == 3 * len(cfg["layers"]["transmission"]["overpass_urls"])


def test_distances_beyond_search_radius_are_missing_not_far(plants, cfg):
    far = _layer([("n9", "Far", 345, Point(-100.0, 31.0))], "sub")        # ~180 mi away
    out = t.compute(plants, _layer([], "line"), far, cfg, "osm", "2026-10-04")
    assert out["dist_345kv_sub_mi"].isna().all() and out["nearest_345kv_sub_name"].isna().all()
    assert (out["transmission_search_mi"] == cfg["layers"]["transmission"]["search_radius_mi"]).all()


def test_report_table_joins_plant_names_and_filters_county(tmp_path, monkeypatch):
    from pipeline.phase_4_geo import report as rp

    layer = pd.DataFrame({"eia_id": [1, 2], "dist_345kv_sub_mi": [3.0, 9.0], "nearest_345kv_sub_name": ["A", "B"],
                          "dist_345kv_line_mi": [1.0, 2.0], "dist_138kv_line_mi": [0.5, 0.6],
                          "kv_classes_within_near": ["345", None], "poi_kv_seen": [True, False],
                          "transmission_confidence": ["medium", "medium"]})
    plants = pd.DataFrame({"eia_id": [1, 2], "plant_name": ["P1", "P2"], "county": ["Pecos", "Ward"], "tier": ["T1b", "T2"],
                           "ac_mw": [100.0, 50.0], "grid_voltage_kv": [345.0, 138.0]})
    (tmp_path / "layers").mkdir()
    layer.to_parquet(tmp_path / "layers" / "transmission.parquet")
    plants.to_parquet(tmp_path / "plants.parquet")
    monkeypatch.setattr(rp, "LAYERS_DIR", tmp_path / "layers")
    monkeypatch.setattr(rp, "DATA_DIR", tmp_path)
    out = rp.table("transmission", "pecos")
    assert list(out["plant_name"]) == ["P1"] and out.iloc[0]["dist_345kv_sub_mi"] == 3.0


OSM_XML = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="31.00" lon="-103.00"/>
  <node id="2" lat="31.10" lon="-103.00"/>
  <node id="3" lat="31.20" lon="-103.00"/>
  <node id="4" lat="31.00" lon="-103.10"/>
  <node id="5" lat="31.01" lon="-103.10"/>
  <node id="6" lat="31.01" lon="-103.09"/>
  <node id="7" lat="31.00" lon="-103.09"/>
  <node id="8" lat="31.50" lon="-103.50"><tag k="power" v="substation"/><tag k="voltage" v="345000;138000"/><tag k="name" v="Solstice"/></node>
  <node id="9" lat="31.60" lon="-103.50"><tag k="power" v="substation"/><tag k="voltage" v="69000"/></node>
  <way id="100"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="power" v="line"/><tag k="voltage" v="345000"/></way>
  <way id="101"><nd ref="1"/><nd ref="2"/><tag k="power" v="line"/><tag k="voltage" v="69000"/></way>
  <way id="102"><nd ref="4"/><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="4"/><tag k="power" v="substation"/><tag k="voltage" v="138000"/><tag k="name" v="Pad"/></way>
  <way id="103"><nd ref="1"/><nd ref="3"/><tag k="highway" v="road"/></way>
</osm>
"""


def test_read_pbf_extracts_high_voltage_lines_and_substations(tmp_path):
    pytest.importorskip("osmium")
    f = tmp_path / "mini.osm"
    f.write_text(OSM_XML)
    lines, subs = t.read_pbf(f, 100)
    assert list(lines["osm_id"]) == ["w100"] and lines.iloc[0]["voltage_kv"] == 345         # 69 kV line and the road are dropped
    assert lines.iloc[0].geometry.geom_type == "LineString" and len(lines.iloc[0].geometry.coords) == 3
    by = subs.set_index("osm_id")
    assert set(by.index) == {"n8", "w102"}                                                  # the 69 kV node is dropped
    assert by.loc["n8", "name"] == "Solstice" and by.loc["n8", "voltage_kv"] == 345
    assert by.loc["w102"].geometry.geom_type == "Point"                                     # area reduced to a centroid
    assert by.loc["w102"].geometry.x == pytest.approx(-103.095, abs=0.01)


def test_run_falls_back_to_overpass_when_the_extract_fails(plants, cfg, tmp_path, monkeypatch):
    from pipeline.phase_4_geo import common as gc

    monkeypatch.setattr(t, "raw_dir", lambda s: tmp_path / s)
    (tmp_path / "transmission").mkdir()
    monkeypatch.setattr(t, "fetch_pbf", lambda cfg: (_ for _ in ()).throw(RuntimeError("download blocked")))
    monkeypatch.setattr(t.g, "load_plants", lambda: plants)
    monkeypatch.setattr(t.g, "LAYERS_DIR", tmp_path / "layers")
    monkeypatch.setattr(t.g, "write_layer", lambda name, df: df)
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    out = t.run(session=FakeOverpass())
    assert (out["transmission_source"] == "osm (overpass)").all()
    assert out.set_index("eia_id").loc[1, "dist_345kv_sub_mi"] == pytest.approx(0.1 * 69.0, rel=0.05)
