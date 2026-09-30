"""USPVDB v4.0 (USGS/LBNL) — PV array polygons with eia_id, p_area, p_cap_ac/dc, p_axis, p_tech_pri, p_year.

Writes data/raw/uspvdb/uspvdb.parquet (GeoParquet, all states, untransformed apart from lower-cased columns).
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import geopandas as gpd

from pipeline.common import download, load_config, manual_file, raw_dir

log = logging.getLogger(__name__)

SOURCE = "uspvdb"
OUT_NAME = "uspvdb.parquet"
# Preference order when a zip holds more than one vector format.
VECTOR_SUFFIXES = (".gpkg", ".shp", ".geojson", ".json")


def _vector_path_in_zip(zpath: Path) -> str:
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
    for suffix in VECTOR_SUFFIXES:
        hits = [n for n in names if n.lower().endswith(suffix) and not n.startswith("__MACOSX")]
        if hits:
            return f"/vsizip/{zpath}/{hits[0]}"
    raise ValueError(f"no vector file in {zpath}: {names[:20]}")


def fetch() -> Path:
    """Return a local path to the USPVDB archive or vector file: manual override first, then URLs."""
    manual = manual_file(SOURCE, (".zip",) + VECTOR_SUFFIXES)
    if manual:
        log.info("using manual USPVDB file %s", manual)
        return manual
    cfg = load_config()["sources"]["uspvdb"]
    errors = []
    for url in cfg["urls"]:
        dest = raw_dir(SOURCE) / Path(url).name
        try:
            return download(url, dest)
        except Exception as e:  # try the next candidate
            errors.append(f"{url}: {e}")
    raise RuntimeError(
        "USPVDB download failed; put the v4.0 GeoPackage/Shapefile zip in data/raw/uspvdb/manual/.\n  "
        + "\n  ".join(errors)
    )


def read(path: Path) -> gpd.GeoDataFrame:
    src = _vector_path_in_zip(path) if path.suffix.lower() == ".zip" else str(path)
    gdf = gpd.read_file(src)
    gdf.columns = [c.lower() if c != "geometry" else c for c in gdf.columns]
    missing = {"eia_id", "p_area", "p_cap_ac", "p_state"} - set(gdf.columns)
    if missing:
        raise ValueError(f"USPVDB schema changed — missing {sorted(missing)}; columns: {list(gdf.columns)}")
    return gdf


def run() -> Path:
    gdf = read(fetch())
    out = raw_dir(SOURCE) / OUT_NAME
    gdf.to_parquet(out)
    log.info("USPVDB: %d polygons (%d in TX) → %s", len(gdf), int((gdf["p_state"] == "TX").sum()), out)
    return out
