"""Pipeline state in Supabase Storage (private bucket `pipeline-state`), so the monthly GitHub Actions job can load results.

Why: the CI runner starts empty. Phases 1, 2 and 5 need what was built elsewhere (the Phase 4 layers, parcels, the Phase 3b
market metrics). Loading a plant master without them would silently regress the database (parcel footprint basis lost, metrics
dropped, layers cascade-deleted when a plant is re-filtered), so CI pulls this state first and refuses to load without it.

    python -m pipeline.state push     # after a local run: data/layers/*.parquet + plant_metrics*.parquet -> bucket
    python -m pipeline.state pull     # CI / a new machine: bucket -> data/ ; exits 1 if there is no state yet
    python -m pipeline.state status   # what is stored and when

Not included (large, rebuildable, or in git): raw caches, SCED/SPP days, crosswalk CSV, load_pockets.csv, offtake.csv.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

import requests

from pipeline.common import DATA_DIR, load_env
from pipeline.load_supabase import _send, headers

log = logging.getLogger(__name__)
BUCKET = "pipeline-state"
MANIFEST = "manifest.json"
PATTERNS = ("layers/*.parquet", "plant_metrics.parquet", "plant_metrics_monthly.parquet")   # relative to data/


def state_files(data_dir: Path = DATA_DIR) -> list[Path]:
    return sorted({p for pat in PATTERNS for p in data_dir.glob(pat)})


def _creds() -> tuple[str, str]:
    load_env()
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SECRET_KEY must be set (pipeline/.env or environment)")
    return url, key


def push(url: str, key: str, data_dir: Path = DATA_DIR, session=requests) -> dict:
    files = state_files(data_dir)
    if not files:
        raise SystemExit(f"nothing to push: no layers or metrics under {data_dir}")
    h = headers(key)
    manifest = {"pushed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "files": {}}
    for p in files:
        rel = p.relative_to(data_dir).as_posix()
        r = _send(session, "post", f"{url}/storage/v1/object/{BUCKET}/{rel}", data=p.read_bytes(), timeout=120,
                  headers=h | {"x-upsert": "true", "Content-Type": "application/octet-stream"})
        if not r.ok:
            raise RuntimeError(f"upload {rel} failed: HTTP {r.status_code} {r.text[:300]}")
        manifest["files"][rel] = p.stat().st_size
    r = _send(session, "post", f"{url}/storage/v1/object/{BUCKET}/{MANIFEST}", data=json.dumps(manifest).encode(), timeout=60,
              headers=h | {"x-upsert": "true", "Content-Type": "application/json"})
    if not r.ok:
        raise RuntimeError(f"manifest upload failed: HTTP {r.status_code} {r.text[:300]}")
    log.info("state pushed: %d files, %.1f MB", len(files), sum(manifest["files"].values()) / 1e6)
    return manifest


def read_manifest(url: str, key: str, session=requests) -> dict | None:
    r = _send(session, "get", f"{url}/storage/v1/object/authenticated/{BUCKET}/{MANIFEST}", timeout=60, headers=headers(key))
    if r.status_code in (400, 404):
        return None
    if not r.ok:
        raise RuntimeError(f"manifest read failed: HTTP {r.status_code} {r.text[:300]}")
    return json.loads(r.content)


def pull(url: str, key: str, data_dir: Path = DATA_DIR, session=requests) -> dict:
    m = read_manifest(url, key, session)
    if m is None:
        raise SystemExit("no pipeline state in Supabase Storage yet: run `python -m pipeline.state push` on the machine that built "
                         "the layers and metrics")
    h = headers(key)
    for rel, size in m["files"].items():
        r = _send(session, "get", f"{url}/storage/v1/object/authenticated/{BUCKET}/{rel}", timeout=120, headers=h)
        if not r.ok:
            raise RuntimeError(f"download {rel} failed: HTTP {r.status_code} {r.text[:300]}")
        if len(r.content) != size:
            raise RuntimeError(f"{rel}: expected {size} bytes, got {len(r.content)}")
        dest = data_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
    log.info("state pulled: %d files (pushed %s)", len(m["files"]), m.get("pushed_at"))
    return m


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cmd = (argv if argv is not None else sys.argv[1:] or [""])[0]
    if cmd not in ("push", "pull", "status"):
        raise SystemExit("usage: python -m pipeline.state push|pull|status")
    url, key = _creds()
    if cmd == "push":
        push(url, key)
    elif cmd == "pull":
        pull(url, key)
    else:
        m = read_manifest(url, key)
        print("no state stored" if m is None else f"pushed {m['pushed_at']}: " + ", ".join(f"{k} ({v:,} B)" for k, v in m["files"].items()))


if __name__ == "__main__":
    main()
