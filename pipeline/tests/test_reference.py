"""Map reference layers: 345 kV substation filter, load-pocket projects with the qualifies flag, loader columns."""
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from pipeline.common import load_config
from pipeline.phase_4_geo import reference as rf


def test_substations_345_filters_and_projects_to_lat_lon():
    subs = gpd.GeoDataFrame({"osm_id": ["a", "b"], "name": ["Big", None], "operator": [None, None], "voltage_kv": [345.0, 138.0]},
                            geometry=[Point(-103.1, 31.2), Point(-97.0, 30.0)], crs="EPSG:4326")
    out = rf.substations_345(subs)
    assert list(out["osm_id"]) == ["a"] and out.iloc[0]["lat"] == 31.2 and out.iloc[0]["lon"] == -103.1


def test_projects_flag_qualifying():
    c = load_config()["layers"]["load_pocket"]
    base = dict(developer=None, county=None, city=None, location_confidence="city", source_url="u", source_date="2026",
                lat=31.0, lon=-102.0)
    t = pd.DataFrame([base | dict(project_id="dc", name="DC", kind="data_center", status="operating", load_mw=np.nan),
                      base | dict(project_id="btc", name="BTC", kind="crypto", status="operating", load_mw=50.0),
                      base | dict(project_id="dead", name="X", kind="data_center", status="cancelled", load_mw=100.0)])
    out = rf.projects(t, c).set_index("project_id")["qualifies"]
    assert out.to_dict() == {"dc": True, "btc": False, "dead": False}


def test_columns_match_migration():
    sql = next(Path("supabase/migrations").glob("*_ref_map_layers.sql")).read_text(encoding="utf-8")
    for table, (_, _, columns) in {v[0]: v for v in rf.TABLES.values()}.items():
        body = re.search(rf"create table public\.{table} \((.*?)\n\);", sql, re.S).group(1)
        cols = [m.group(1) for line in body.splitlines() if (m := re.match(r"\s*([a-z_0-9]+)\s", line.split("--")[0]))]
        assert [c for c in cols if c != "loaded_at"] == columns
