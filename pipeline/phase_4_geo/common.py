"""Helpers shared by every Phase 4 layer."""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from pipeline.common import DATA_DIR

log = logging.getLogger(__name__)

LAYERS_DIR = DATA_DIR / "layers"
METRIC_CRS = "EPSG:5070"        # CONUS Albers, metres
M_PER_MI = 1609.344


def load_plants(path: Path | None = None) -> gpd.GeoDataFrame:
    """plants.parquet with a usable geometry for every plant: the USPVDB array polygon, else the EIA lat/lon point."""
    plants = gpd.read_parquet(path or DATA_DIR / "plants.parquet")
    plants = plants[plants["filter_status"].isin(["pass", "review"])].copy()
    return with_point_fallback(plants)


def with_point_fallback(plants: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    geom = plants.geometry
    missing = geom.isna() | geom.is_empty
    if missing.any():
        pts = [Point(lo, la) if pd.notna(lo) and pd.notna(la) else None for lo, la in zip(plants["lon"], plants["lat"])]
        crs = plants.crs
        pts = gpd.GeoSeries(pts, index=plants.index, crs="EPSG:4326").to_crs(crs) if crs else gpd.GeoSeries(pts, index=plants.index)
        plants = plants.copy()
        plants.loc[missing, plants.geometry.name] = pts[missing]
        plants["geometry_is_point"] = missing
    else:
        plants = plants.copy()
        plants["geometry_is_point"] = False
    return plants


def nearest(plants: gpd.GeoDataFrame, feats: gpd.GeoDataFrame, keep: list[str], prefix: str) -> pd.DataFrame:
    """Per eia_id: distance in miles (edge to edge) to the nearest feature, plus the `keep` attributes of that feature.

    Columns out: eia_id, <prefix>_mi, <prefix>_<attr>...  Empty `feats` gives NaN, never 0."""
    out = pd.DataFrame({"eia_id": plants["eia_id"].to_numpy()})
    cols = [f"{prefix}_mi"] + [f"{prefix}_{k}" for k in keep]
    if feats is None or feats.empty:
        for c in cols:
            out[c] = np.nan if c.endswith("_mi") else None
        return out
    left = plants[["eia_id", plants.geometry.name]].to_crs(METRIC_CRS)
    right = feats[[*keep, feats.geometry.name]].to_crs(METRIC_CRS)
    j = gpd.sjoin_nearest(left, right, how="left", distance_col="_d")
    j = j.sort_values("_d").drop_duplicates("eia_id")
    j[f"{prefix}_mi"] = j["_d"] / M_PER_MI
    for k in keep:
        j[f"{prefix}_{k}"] = j[k]
    return out.merge(j[["eia_id", *cols]], on="eia_id", how="left")


def within(plants: gpd.GeoDataFrame, feats: gpd.GeoDataFrame, miles: float) -> dict[int, np.ndarray]:
    """eia_id → positional indices of `feats` within `miles` of the plant (edge to edge)."""
    if feats is None or feats.empty:
        return {int(i): np.array([], dtype=int) for i in plants["eia_id"]}
    P = plants.to_crs(METRIC_CRS)
    F = feats.to_crs(METRIC_CRS)
    tree = F.sindex
    out = {}
    for pid, g in zip(P["eia_id"], P.geometry):
        out[int(pid)] = np.sort(tree.query(g.buffer(miles * M_PER_MI), predicate="intersects"))
    return out


def write_layer(name: str, df: pd.DataFrame) -> Path:
    LAYERS_DIR.mkdir(parents=True, exist_ok=True)
    path = LAYERS_DIR / f"{name}.parquet"
    df.to_parquet(path, index=False)
    log.info("layer %s: %d plants → %s", name, len(df), path)
    return path
