"""Load data/scores.parquet into Supabase plant_scores (upsert on eia_id, drop rows of earlier runs)."""
from __future__ import annotations

import datetime as dt
import logging
import os

import pandas as pd

from pipeline.common import DATA_DIR, load_env
from pipeline.phase_4_geo import load as layer_load

log = logging.getLogger(__name__)

# Must match supabase/migrations/*_plant_scores.sql (minus loaded_at) followed by later `add column`s; a test enforces it.
SCORE_COLUMNS = [
    "eia_id", "score_total", "score_A", "score_B", "score_C", "score_D", "score_E", "score_F",
    "pts_capture", "pts_curtailment", "pts_cf_benchmark", "pts_offtake", "pts_poly", "pts_monofacial", "pts_tracker",
    "pts_acres", "pts_headroom", "pts_land_control", "pts_poi_kv", "pts_dist_345", "pts_load_pocket",
    "pts_lambda", "pts_drill", "pts_hours25", "pts_water", "pts_fixed_cost",
    "data_completeness", "missing_inputs", "rank_overall", "rank_in_tier", "region", "cf_used", "cf_source",
    "cf_benchmark", "cf_below_benchmark_pts", "offtake_confidence", "fixed_cost_est_usd", "fixed_cost_includes_fiber",
    "fixed_cost_per_kw_it", "thermal_response_test_required", "score_version", "run_id",
    "fiber_lateral_miles",   # added by *_layers_fiber.sql (alter table)
    # added by *_plant_scores_offtake.sql (alter table)
    "offtake_status", "offtake_type", "offtake_counterparty", "offtake_counterparty_ig", "offtake_contract_end",
    "offtake_years_left", "offtake_expired", "offtake_share_contracted", "offtake_source_url", "offtake_source_date",
]


def run() -> int:
    load_env()
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SECRET_KEY must be set (pipeline/.env or environment)")
    path = DATA_DIR / "scores.parquet"
    if not path.exists():
        raise SystemExit(f"{path} is missing: run --phase 5 first")
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    df = pd.read_parquet(path)
    # Postgres folds unquoted identifiers to lower case: score_A → score_a
    df = df.rename(columns={c: c.lower() for c in df.columns})
    cols = [c.lower() for c in SCORE_COLUMNS]
    return layer_load.load_layer("scores", df, url, key, run_id, table="plant_scores", columns=cols)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    run()
