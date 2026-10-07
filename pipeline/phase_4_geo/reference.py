"""Reference point layers for the web map: 345 kV substations (transmission layer's OSM cache) and the load-pocket
projects (data/load_pockets.csv). Not per-plant; loaded to Supabase ref_* tables alongside their layer
(`--phase 4 --layer transmission|load_pocket --load`), replacing the previous rows."""
from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

from pipeline.common import load_config, raw_dir
from pipeline.load_supabase import _clean, _send, headers
from pipeline.phase_4_geo import load_pocket as lp

log = logging.getLogger(__name__)

# Must match supabase/migrations/*_ref_map_layers.sql (minus loaded_at); a test enforces it.
SUBSTATION_COLUMNS = ["osm_id", "name", "operator", "voltage_kv", "lat", "lon", "run_id"]
PROJECT_COLUMNS = ["project_id", "name", "developer", "kind", "status", "load_mw", "county", "city", "lat", "lon",
                   "location_confidence", "qualifies", "source_url", "source_date", "run_id"]
TABLES = {"transmission": ("ref_substations_345kv", "osm_id", SUBSTATION_COLUMNS),
          "load_pocket": ("ref_load_pocket_projects", "project_id", PROJECT_COLUMNS)}
BATCH = 500


def substations_345(subs: gpd.GeoDataFrame, min_kv: float = 345) -> pd.DataFrame:
    s = subs[subs["voltage_kv"] >= min_kv].to_crs("EPSG:4326")
    return pd.DataFrame({"osm_id": s["osm_id"], "name": s["name"], "operator": s["operator"],
                         "voltage_kv": s["voltage_kv"], "lat": s.geometry.y.round(5), "lon": s.geometry.x.round(5)})


def projects(table: pd.DataFrame, c: dict) -> pd.DataFrame:
    q = set(lp.qualifying(table, c)["project_id"])
    out = table[["project_id", "name", "developer", "kind", "status", "load_mw", "county", "city", "lat", "lon",
                 "location_confidence", "source_url", "source_date"]].copy()
    out["qualifies"] = out["project_id"].isin(q)
    return out[out["lat"].notna() & out["lon"].notna()]


def frame(name: str) -> pd.DataFrame | None:
    """The reference rows for a layer, or None when its source is not on this machine."""
    cfg = load_config()
    if name == "transmission":
        p = raw_dir("transmission") / "pbf_substations.parquet"
        return substations_345(gpd.read_parquet(p)) if p.exists() else None
    if name == "load_pocket":
        p = lp.table_path(cfg)
        return projects(lp.read_table(p), cfg["layers"]["load_pocket"]) if p.exists() else None
    return None


def replace(table: str, key: str, columns: list[str], df: pd.DataFrame, url: str, secret: str, run_id: str,
            session=requests) -> int:
    """Upsert every row on `key` with this run_id, then delete rows of earlier runs."""
    d = df.copy()
    d["run_id"] = run_id
    rows = [{c: _clean(r[c], c) for c in columns} for _, r in d.iterrows()]
    base = f"{url.rstrip('/')}/rest/v1/{table}"
    h = headers(secret) | {"Content-Type": "application/json"}
    for i in range(0, len(rows), BATCH):
        r = _send(session, "post", f"{base}?on_conflict={key}", json=rows[i:i + BATCH], timeout=60,
                  headers=h | {"Prefer": "resolution=merge-duplicates,return=minimal"})
        if not r.ok:
            raise RuntimeError(f"{table} upsert failed: HTTP {r.status_code} {r.text[:500]}")
    r = _send(session, "delete", f"{base}?run_id=neq.{run_id}", headers=h | {"Prefer": "return=minimal"}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"{table} stale-row delete failed: HTTP {r.status_code} {r.text[:500]}")
    log.info("Supabase %s: %d rows loaded (run %s)", table, len(rows), run_id)
    return len(rows)


def load(name: str, url: str, secret: str, run_id: str, session=requests) -> int | None:
    if name not in TABLES:
        return None
    df = frame(name)
    if df is None:
        log.warning("reference rows for %s: source not on this machine, %s left as is", name, TABLES[name][0])
        return None
    table, key, columns = TABLES[name]
    return replace(table, key, columns, df.replace({np.nan: None}), url, secret, run_id, session)
