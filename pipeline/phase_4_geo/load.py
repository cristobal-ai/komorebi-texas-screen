"""Load Phase 4 layer parquet files into Supabase tables layers_<name> (upsert on eia_id, drop rows of earlier runs)."""
from __future__ import annotations

import datetime as dt
import logging
import os

import pandas as pd
import requests

from pipeline.common import load_env
from pipeline.load_supabase import _clean, _send, headers
from pipeline.phase_4_geo import common as g
from pipeline.phase_4_geo import reference

log = logging.getLogger(__name__)

# Must match supabase/migrations/*_layers_<name>.sql (minus loaded_at); a test enforces it.
TRANSMISSION_COLUMNS = [
    "eia_id", "dist_345kv_sub_mi", "nearest_345kv_sub_name", "dist_345kv_line_mi", "dist_138kv_line_mi",
    "kv_classes_within_near", "max_kv_within_near", "poi_kv_seen", "transmission_search_mi", "transmission_source", "transmission_fetched",
    "transmission_confidence", "run_id",
]
PARCELS_COLUMNS = [
    "eia_id", "parcel_status", "n_host_parcels", "parcel_acres_host", "parcel_acres_unified", "adjacent_same_owner_acres",
    "host_cover_share", "headroom_pct_host", "headroom_pct_unified", "acres_per_mw_parcel", "host_owners",
    "largest_owner_share", "unified_land_control", "land_use_codes", "mkt_value_total", "land_value_per_acre",
    "parcel_vintage", "parcel_source", "parcels_confidence", "run_id",
    "host_parcel_ids",                 # added by a later migration (ALTER TABLE), hence after run_id
]
GAS_PIPELINES_COLUMNS = [
    "eia_id", "dist_gas_transmission_mi", "gas_transmission_operator", "gas_transmission_system",
    "gas_transmission_diameter_in", "gas_transmission_interstate", "dist_gas_any_mi", "n_gas_transmission_near",
    "max_gas_transmission_diameter_near_in", "gas_search_mi", "gas_source", "gas_fetched", "gas_confidence", "run_id",
]
FLOOD_COLUMNS = [
    "eia_id", "flood_status", "nfhl_mapped_share", "sfha_share", "sfha_acres", "floodway_share", "x500_share",
    "zone_d_share", "flood_zones", "flood_flag", "nfhl_study_ids", "flood_source", "flood_fetched", "flood_confidence",
    "run_id",
]
FIBER_COLUMNS = [
    "eia_id", "dist_class1_rail_mi", "nearest_rail_owner", "nearest_rail_subdiv", "dist_interstate_mi",
    "nearest_interstate", "fiber_lateral_miles", "fiber_corridor", "nearest_carrier_hotel", "carrier_hotel_gc_mi",
    "carrier_hotel_route_mi_est", "latency_rtt_ms_est", "carrier_hotel_rtt_ms", "fiber_search_mi", "fiber_source",
    "fiber_fetched", "fiber_confidence", "run_id",
]
CLIMATE_COLUMNS = [
    "eia_id", "hours_below_25c_drybulb", "hours_below_25c_drybulb_min", "hours_below_25c_drybulb_tmy",
    "hours_below_25c_by_year", "hours_below_15c_drybulb", "hours_below_20c_drybulb", "hours_below_20c_wetbulb",
    "hours_above_35c_drybulb", "mean_annual_temp_c", "design_drybulb_0p4_c", "design_wetbulb_0p4_c", "max_drybulb_c",
    "nsrdb_location_id", "nsrdb_lat", "nsrdb_lon", "nsrdb_elevation_m", "climate_years", "climate_source",
    "climate_fetched", "climate_confidence", "run_id",
]
WELLS_COLUMNS = [
    "eia_id", "n_logs", "logs_radius_mi", "thick_hard_layer_share", "hard_layer_ft_median", "hard_layer_ft_p90",
    "caliche_log_share", "gypsum_log_share", "hard_rock_log_share", "lost_circulation_log_share", "median_log_depth_ft",
    "n_geothermal_bores", "n_water_levels", "water_radius_mi", "depth_to_water_ft", "depth_to_water_ft_p25",
    "depth_to_water_ft_p75", "water_level_sources", "latest_water_level_year", "aquifer_majority", "gcd_majority",
    "wells_source", "wells_fetched", "wells_confidence", "run_id",
]
SOILS_COLUMNS = [
    "eia_id", "soil_status", "soil_lambda_w_mk", "soil_lambda_dry_w_mk", "soil_lambda_sat_w_mk", "soil_sand_pct",
    "soil_clay_pct", "soil_bulk_density", "soil_theta_fc", "dominant_soil", "dominant_mapunit", "n_mapunits",
    "soil_data_share", "restriction_kinds", "restriction_min_depth_cm", "restriction_share", "bedrock_depth_cm_min",
    "soil_source", "soil_fetched", "soil_confidence", "run_id",
]
LOAD_POCKET_COLUMNS = [
    "eia_id", "dist_load_pocket_firm_mi", "nearest_firm_project", "nearest_firm_kind", "nearest_firm_status",
    "nearest_firm_mw", "dist_load_pocket_announced_mi", "nearest_announced_project", "nearest_announced_kind",
    "nearest_announced_mw", "n_projects_within_search", "mw_within_search", "projects_within_search",
    "load_pocket_location_confidence", "load_pocket_search_mi", "load_pocket_source", "load_pocket_table_date",
    "load_pocket_confidence", "run_id",
]
LAYER_COLUMNS = {"transmission": TRANSMISSION_COLUMNS, "parcels": PARCELS_COLUMNS,
                 "gas_pipelines": GAS_PIPELINES_COLUMNS, "flood": FLOOD_COLUMNS, "fiber": FIBER_COLUMNS,
                 "climate": CLIMATE_COLUMNS, "wells": WELLS_COLUMNS, "soils": SOILS_COLUMNS,
                 "load_pocket": LOAD_POCKET_COLUMNS}
BATCH = 100


def to_rows(df: pd.DataFrame, columns: list[str], run_id: str) -> list[dict]:
    d = df.copy()
    d["run_id"] = run_id
    for c in columns:
        if c not in d.columns:
            d[c] = None
    return [{c: _clean(r[c], c) for c in columns} for _, r in d.iterrows()]


def load_layer(name: str, df: pd.DataFrame, url: str, key: str, run_id: str, plant_ids: set[int] | None = None,
               session=requests, table: str | None = None, columns: list[str] | None = None) -> int:
    """Upsert one row per eia_id into `table` (default layers_<name>), then delete rows of earlier runs and check the count."""
    table = table or f"layers_{name}"
    if plant_ids is not None:
        df = df[df["eia_id"].isin(plant_ids)]
    rows = to_rows(df, columns or LAYER_COLUMNS[name], run_id)
    base = f"{url.rstrip('/')}/rest/v1/{table}"
    h = headers(key) | {"Content-Type": "application/json"}
    for i in range(0, len(rows), BATCH):
        r = _send(session, "post", f"{base}?on_conflict=eia_id", json=rows[i:i + BATCH], timeout=60,
                  headers=h | {"Prefer": "resolution=merge-duplicates,return=minimal"})
        if not r.ok:
            raise RuntimeError(f"{table} upsert batch {i // BATCH} failed: HTTP {r.status_code} {r.text[:500]}")
    r = _send(session, "delete", f"{base}?run_id=neq.{run_id}", headers=h | {"Prefer": "return=minimal"}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"{table} stale-row delete failed: HTTP {r.status_code} {r.text[:500]}")
    r = _send(session, "get", f"{base}?select=eia_id&run_id=eq.{run_id}", timeout=60,
              headers=h | {"Prefer": "count=exact", "Range": "0-0"})
    if not r.ok:
        raise RuntimeError(f"{table} count check failed: HTTP {r.status_code} {r.text[:500]}")
    n = int(r.headers.get("Content-Range", "*/-1").split("/")[-1])
    if n != len(rows):
        raise RuntimeError(f"{table} row count mismatch: sent {len(rows)}, table has {n} for run {run_id}")
    log.info("Supabase %s: %d rows loaded (run %s)", table, n, run_id)
    return n


def run(names: list[str]) -> None:
    load_env()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SECRET_KEY must be set (pipeline/.env or environment)")
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    plants = g.load_plants()
    for name in names:
        path = g.LAYERS_DIR / f"{name}.parquet"
        if not path.exists():
            raise SystemExit(f"{path} is missing: run --phase 4 --layer {name} first")
        load_layer(name, pd.read_parquet(path), url, key, run_id, set(plants["eia_id"].astype(int)))
        reference.load(name, url, key, run_id)        # map overlay points that belong to this layer, if any


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    run([n for n in sorted(LAYER_COLUMNS) if (g.LAYERS_DIR / f"{n}.parquet").exists()])
