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

log = logging.getLogger(__name__)

# Must match supabase/migrations/*_layers_<name>.sql (minus loaded_at); a test enforces it.
TRANSMISSION_COLUMNS = [
    "eia_id", "dist_345kv_sub_mi", "nearest_345kv_sub_name", "dist_345kv_line_mi", "dist_138kv_line_mi",
    "kv_classes_within_near", "max_kv_within_near", "poi_kv_seen", "transmission_source", "transmission_fetched",
    "transmission_confidence", "run_id",
]
LAYER_COLUMNS = {"transmission": TRANSMISSION_COLUMNS}
BATCH = 100


def to_rows(df: pd.DataFrame, columns: list[str], run_id: str) -> list[dict]:
    d = df.copy()
    d["run_id"] = run_id
    for c in columns:
        if c not in d.columns:
            d[c] = None
    return [{c: _clean(r[c], c) for c in columns} for _, r in d.iterrows()]


def load_layer(name: str, df: pd.DataFrame, url: str, key: str, run_id: str, plant_ids: set[int] | None = None,
               session=requests) -> int:
    table = f"layers_{name}"
    if plant_ids is not None:
        df = df[df["eia_id"].isin(plant_ids)]
    rows = to_rows(df, LAYER_COLUMNS[name], run_id)
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    run(sorted(LAYER_COLUMNS))
