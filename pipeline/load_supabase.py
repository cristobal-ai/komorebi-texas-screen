"""Write the phase 1–2 plant master to Supabase `public.plants` (migration supabase/migrations/*_plants.sql).

    python -m pipeline.run --phase 2 --county Pecos --load

Loads plants with filter_status 'pass' or 'review' (fail stays in data/plants.parquet only). Upserts on eia_id,
then deletes rows from earlier runs that are no longer in the set, then verifies the row count. Uses PostgREST over
HTTPS with the secret key in the apikey header (same call check_env verifies), so no SDK key-format assumptions.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

from pipeline.common import DATA_DIR, load_env

log = logging.getLogger(__name__)

TABLE = "plants"
LOADED_STATUSES = ("pass", "review")
BATCH = 50
# Must match the column list in supabase/migrations/*_plants.sql (minus geom and loaded_at); a test enforces it.
PLANT_COLUMNS = [
    "eia_id", "plant_name", "operator", "county", "ba_code", "lat", "lon",
    "ac_mw", "n_generators", "cod_first", "cod_last", "cod_multi_phase", "has_colocated_storage", "source_month",
    "dc_mw", "dc_mw_source", "ilr", "ilr_confidence",
    "uspvdb_match", "ac_mw_uspvdb", "ac_mw_delta_pct", "year_uspvdb", "tracking_uspvdb", "n_polygons",
    "array_acres", "array_acres_calc", "acres_per_mw_ac", "footprint_basis",
    "filter_status", "filter_reasons", "non_ercot_texas", "tier", "firm_it_mw", "planned_load_mw",
    "sb6_review_required", "tax_equity_consent_likely", "itc_recapture_open",
    "eia860_year", "grid_voltage_kv", "grid_voltage_max_kv", "grid_voltage_source", "distribution_class_poi",
    "tracking_type", "tracking_type_share", "module_tech", "module_tech_share", "module_type_confidence",
    "bifacial_share",
    "cf_year", "net_mwh", "net_ac_cf", "cf_series_resolution", "months_reported", "cf_note",
    "run_id",
]
INT_COLUMNS = {"eia_id", "n_generators", "year_uspvdb", "n_polygons", "eia860_year", "cf_year", "months_reported"}


def headers(key: str) -> dict:
    """PostgREST auth headers. New sb_ keys go in apikey only; a legacy service_role JWT also needs Bearer."""
    h = {"apikey": key}
    if not key.startswith("sb_"):
        h["Authorization"] = f"Bearer {key}"
    return h


def _clean(v, col: str):
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, (float, np.floating)) and math.isnan(v):
        return None
    if isinstance(v, (pd.Timestamp, dt.datetime, dt.date)):
        return v.date().isoformat() if isinstance(v, (pd.Timestamp, dt.datetime)) else v.isoformat()
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if col in INT_COLUMNS:
        return int(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v


def to_rows(plants: gpd.GeoDataFrame, run_id: str) -> list[dict]:
    p = plants[plants["filter_status"].isin(LOADED_STATUSES)].copy()
    p["run_id"] = run_id
    for c in PLANT_COLUMNS:
        if c not in p.columns:
            p[c] = None
    geoms = p.geometry.to_crs(4326) if p.geometry.crs is not None else p.geometry
    rows = []
    for (_, r), g in zip(p.iterrows(), geoms):
        row = {c: _clean(r[c], c) for c in PLANT_COLUMNS}
        row["geom"] = None if g is None or g.is_empty else f"SRID=4326;{g.wkt}"
        rows.append(row)
    return rows


def load(plants: gpd.GeoDataFrame, url: str, key: str, run_id: str | None = None, session=requests) -> int:
    run_id = run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows = to_rows(plants, run_id)
    base = f"{url.rstrip('/')}/rest/v1/{TABLE}"
    h = headers(key) | {"Content-Type": "application/json"}
    for i in range(0, len(rows), BATCH):
        r = session.post(f"{base}?on_conflict=eia_id", json=rows[i:i + BATCH], timeout=60,
                         headers=h | {"Prefer": "resolution=merge-duplicates,return=minimal"})
        if not r.ok:
            raise RuntimeError(f"upsert batch {i // BATCH} failed: HTTP {r.status_code} {r.text[:500]}")
    r = session.delete(f"{base}?run_id=neq.{run_id}", headers=h | {"Prefer": "return=minimal"}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"stale-row delete failed: HTTP {r.status_code} {r.text[:500]}")
    r = session.get(f"{base}?select=eia_id&run_id=eq.{run_id}", timeout=60,
                    headers=h | {"Prefer": "count=exact", "Range": "0-0"})
    if not r.ok:
        raise RuntimeError(f"count check failed: HTTP {r.status_code} {r.text[:500]}")
    n = int(r.headers.get("Content-Range", "*/-1").split("/")[-1])
    if n != len(rows):
        raise RuntimeError(f"row count mismatch after load: sent {len(rows)}, table has {n} for run {run_id}")
    log.info("Supabase %s: %d rows loaded (run %s)", TABLE, n, run_id)
    return n


def run() -> int:
    load_env()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SECRET_KEY must be set (pipeline/.env or environment)")
    return load(gpd.read_parquet(DATA_DIR / "plants.parquet"), url, key)
