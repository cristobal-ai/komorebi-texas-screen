"""FCC BDC fiber layer: chunk reduction, H3 placement, distances and provider counts, cache/zip reading, migration contract."""
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from pipeline.common import load_config
from pipeline.phase_4_geo import common as g
from pipeline.phase_4_geo import fiber_bdc as fb
from pipeline.phase_4_geo import load as ld

h3 = pytest.importorskip("h3")
CFG = load_config()
LAT, LON = 31.0, -103.0                      # Pecos County
SITE = h3.latlng_to_cell(LAT, LON, 8)


def csv_frame(rows):
    cols = ["frn", "provider_id", "brand_name", "location_id", "technology", "max_advertised_download_speed",
            "max_advertised_upload_speed", "low_latency", "business_residential_code", "state_usps", "block_geoid", "h3_res8_id"]
    return pd.DataFrame([dict(zip(cols, ["1", *r])) for r in rows], dtype=str)


def plant(lon=LON, lat=LAT, half_m=500, point=False):
    c = gpd.GeoSeries(gpd.points_from_xy([lon], [lat]), crs="EPSG:4326").to_crs(g.METRIC_CRS).iloc[0]
    poly = box(c.x - half_m, c.y - half_m, c.x + half_m, c.y + half_m)
    return gpd.GeoDataFrame({"eia_id": [1], "geometry_is_point": [point]}, geometry=[poly], crs=g.METRIC_CRS)


def test_reduce_keeps_only_fiber_and_aggregates_per_cell_provider():
    d = csv_frame([("10", "Acme Fiber", "L1", "50", "1000", "1000", "1", "R", "48", "b", SITE),
                   ("10", "Acme Fiber", "L2", "50", "2000", "1000", "1", "B", "48", "b", SITE.upper()),
                   ("11", "Cable Co", "L3", "40", "500", "50", "1", "R", "48", "b", SITE)])
    r = fb.reduce_chunk(d, CFG)
    assert len(r) == 1 and r.iloc[0]["locations"] == 2 and r.iloc[0]["max_down"] == 2000 and bool(r.iloc[0]["business"])
    assert r.iloc[0]["h3"] == SITE                                   # normalised to lower case
    assert fb.reduce_chunk(d.iloc[2:], CFG).empty
    with pytest.raises(SystemExit):
        fb.reduce_chunk(d.drop(columns=["h3_res8_id"]), CFG)


def cells_table():
    far = h3.latlng_to_cell(LAT + 0.145, LON, 8)                     # ~10 mi north
    return pd.DataFrame({"h3": [SITE, SITE, far], "provider_id": ["10", "11", "12"], "brand": ["Acme Fiber", "Beta Net", "Gamma"],
                         "locations": [4, 2, 9], "max_down": [1000.0, 940.0, 2000.0], "max_up": [1000.0, 940.0, 2000.0],
                         "business": [False, False, True]})


def test_compute_site_and_near_metrics():
    r = fb.compute(plant(), cells_table(), CFG, "x.csv", "J25 30jun2026", "2026-10-14").iloc[0]
    assert r["bdc_fiber_at_site"] and r["bdc_fiber_nearest_mi"] < 0.3
    assert r["bdc_providers_near"] == 2 and r["bdc_cells_near"] == 1 and r["bdc_max_down_mbps_near"] == 1000
    assert r["bdc_business_fiber_near"] is False or not r["bdc_business_fiber_near"]
    assert set(r["bdc_brands_near"].split("; ")) == {"Acme Fiber", "Beta Net"} and r["bdc_confidence"] == "medium"
    assert r["bdc_release"] == "J25 30jun2026"


def test_compute_distance_to_nearest_and_none_within_radius():
    only_far = cells_table().iloc[2:]
    r = fb.compute(plant(), only_far, CFG, "x", None, "2026-10-14").iloc[0]
    assert not r["bdc_fiber_at_site"] and 9.0 < r["bdc_fiber_nearest_mi"] < 10.5
    assert r["bdc_providers_near"] == 0 and r["bdc_cells_near"] == 0 and not r["bdc_business_fiber_near"]
    nothing = fb.compute(plant(lat=34.5), only_far, CFG, "x", None, "2026-10-14").iloc[0]      # ~240 mi away
    assert np.isnan(nothing["bdc_fiber_nearest_mi"]) and not nothing["bdc_fiber_at_site"] and nothing["bdc_nearest_brands"] is None
    assert fb.compute(plant(point=True), only_far, CFG, "x", None, "d").iloc[0]["bdc_confidence"] == "low"


def test_read_inputs_from_zip_in_chunks(tmp_path):
    cfg = {**CFG, "layers": {**CFG["layers"], "fiber_bdc": {**CFG["layers"]["fiber_bdc"], "chunk_rows": 2}}}
    d = csv_frame([("10", "Acme Fiber", f"L{i}", "50", "1000", "100", "1", "R", "48", "b", SITE) for i in range(5)])
    z = tmp_path / "bdc_48_Fiber-to-the-Premises_fixed_broadband_J25_30jun2026.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("data.csv", d.to_csv(index=False))
    r = fb.read_inputs([z], cfg)
    assert len(r) == 1 and r.iloc[0]["locations"] == 5               # chunks of 2 rows merge back to one cell x provider row
    assert fb.release_of(z.name) == "J25 30jun2026"


def test_loader_columns_match_migration():
    sql = next(Path("supabase/migrations").glob("*_layers_fiber_bdc.sql")).read_text(encoding="utf-8")
    block = sql.split("create table public.layers_fiber_bdc (")[1].split("\n);")[0]
    cols = [ln.split()[0] for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("--")]
    assert [c for c in cols if c != "loaded_at"] == ld.LAYER_COLUMNS["fiber_bdc"]
