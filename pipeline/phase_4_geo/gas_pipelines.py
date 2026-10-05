"""Layer 2: natural gas pipelines. Distance from each plant to in-service gas transmission and gathering lines.

Source: Texas RRC public viewer, layer 12 (QPipelines), queried in snap-grid boxes around the plants and cached per box
under data/raw/gas_pipelines/boxes/ (delete the folder to refresh). The same endpoint MapsMaker overlays in the browser.
QPipelines is a generalized line set: positions are good to a few hundred metres, so this is a screen. Display only;
the brief does not score gas proximity.

Output data/layers/gas_pipelines.parquet, one row per eia_id:
    dist_gas_transmission_mi        edge-to-edge miles to the nearest in-service gas transmission line
    gas_transmission_operator, gas_transmission_system, gas_transmission_diameter_in, gas_transmission_interstate
    dist_gas_any_mi                 nearest in-service natural gas line of any system type (gathering included)
    n_gas_transmission_near         gas transmission segments within near_miles
    max_gas_transmission_diameter_near_in
    gas_search_mi                   coverage radius: a null distance means none within this many miles
    gas_source, gas_fetched, gas_confidence
"""
from __future__ import annotations

import datetime as dt
import logging
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

from pipeline.common import load_config, raw_dir
from pipeline.phase_4_geo import arcgis
from pipeline.phase_4_geo import common as g

log = logging.getLogger(__name__)
NAME = "gas_pipelines"
FIELDS = ["OBJECTID", "OPERATOR", "COMMODITY_DESCRIPTION", "SYSTEM_NAME", "SYSTEM_TYPE", "DIAMETER", "INTERSTATE", "STATUS"]


def plant_boxes(plants: gpd.GeoDataFrame, c: dict) -> list[tuple[float, float, float, float]]:
    """Unique (minx, miny, maxx, maxy) boxes centred on a snap grid: plants in the same cell share one query."""
    snap, half = c["bbox_snap_deg"], c["bbox_half_deg"]
    boxes = set()
    for lat, lon in zip(plants["lat"], plants["lon"]):
        if pd.isna(lat) or pd.isna(lon):
            continue
        cy, cx = round(round(lat / snap) * snap, 4), round(round(lon / snap) * snap, 4)
        boxes.add((round(cx - half, 4), round(cy - half, 4), round(cx + half, 4), round(cy + half, 4)))
    return sorted(boxes)


def where_clause(c: dict) -> str:
    comm = ",".join("'" + x.replace("'", "''") + "'" for x in c["commodities"])
    return f"STATUS = '{c['status_in_service']}' AND COMMODITY_DESCRIPTION IN ({comm})"


def _key(b) -> str:
    return "_".join(f"{v:+.2f}".replace(".", "p") for v in b)


def fetch(plants: gpd.GeoDataFrame, cfg: dict, session=requests, sleep=time.sleep) -> tuple[gpd.GeoDataFrame, str]:
    c = cfg["layers"][NAME]
    d = raw_dir(NAME) / "boxes"
    d.mkdir(exist_ok=True)
    boxes = plant_boxes(plants, c)
    frames = []
    for i, b in enumerate(boxes, 1):
        p = d / f"{_key(b)}.parquet"
        if not p.exists():
            got = arcgis.query(c["layer_url"], where_clause(c), FIELDS, b, session=session, sleep=sleep)
            got.to_parquet(p)
            log.info("RRC box %d/%d %s: %d gas line segments", i, len(boxes), b, len(got))
            sleep(c["pause_s"])
        frames.append(gpd.read_parquet(p))
    frames = [f for f in frames if len(f)]
    lines = (gpd.GeoDataFrame(pd.concat(frames, ignore_index=True).drop_duplicates("OBJECTID"), geometry="geometry", crs="EPSG:4326")
             if frames else arcgis.empty(FIELDS))
    stamp = max((dt.datetime.fromtimestamp(f.stat().st_mtime).date() for f in d.glob("*.parquet")), default=dt.date.today())
    return lines, stamp.isoformat()


def is_transmission(system_type: pd.Series, prefixes: list[str]) -> pd.Series:
    s = system_type.fillna("").astype(str).str.strip().str.lower()
    return s.apply(lambda v: any(v.startswith(p.lower()) for p in prefixes))


def compute(plants: gpd.GeoDataFrame, lines: gpd.GeoDataFrame, cfg: dict, fetched: str) -> pd.DataFrame:
    c = cfg["layers"][NAME]
    lines = lines.copy()
    lines["diameter_in"] = pd.to_numeric(lines["DIAMETER"], errors="coerce").where(lambda v: v > 0)   # 0 = not recorded
    lines["interstate"] = lines["INTERSTATE"].map(lambda v: None if v is None or pd.isna(v) else str(v).strip().lower() == "yes")
    tx = lines[is_transmission(lines["SYSTEM_TYPE"], c["transmission_system_types"])] if len(lines) else lines

    out = g.nearest(plants, tx, ["OPERATOR", "SYSTEM_NAME", "diameter_in", "interstate"], "gt")
    out = out.rename(columns={"gt_mi": "dist_gas_transmission_mi", "gt_OPERATOR": "gas_transmission_operator",
                              "gt_SYSTEM_NAME": "gas_transmission_system", "gt_diameter_in": "gas_transmission_diameter_in",
                              "gt_interstate": "gas_transmission_interstate"})
    out = out.merge(g.nearest(plants, lines, [], "dist_gas_any"), on="eia_id")

    near = g.within(plants, tx, c["near_miles"])
    dia = tx["diameter_in"].to_numpy() if len(tx) else np.array([])
    out["n_gas_transmission_near"] = [len(near[int(p)]) for p in out["eia_id"]]
    out["max_gas_transmission_diameter_near_in"] = [
        float(np.nanmax(dia[near[int(p)]])) if len(near[int(p)]) and np.isfinite(dia[near[int(p)]]).any() else np.nan
        for p in out["eia_id"]]

    r = c["search_radius_mi"]
    far = out["dist_gas_transmission_mi"] > r
    out.loc[far, "dist_gas_transmission_mi"] = np.nan     # beyond the covered radius the nearest line may be missing
    for col in ("gas_transmission_operator", "gas_transmission_system", "gas_transmission_diameter_in", "gas_transmission_interstate"):
        out[col] = out[col].astype(object)
        out.loc[out["dist_gas_transmission_mi"].isna(), col] = None
    out.loc[out["dist_gas_any_mi"] > r, "dist_gas_any_mi"] = np.nan
    out["gas_search_mi"] = r
    out["gas_source"] = "rrc qpipelines (layer 12)"
    out["gas_fetched"] = fetched
    out["gas_confidence"] = np.where(plants["geometry_is_point"].to_numpy(), "low", "medium")
    return out


def run(session=requests) -> object:
    cfg = load_config()
    plants = g.load_plants()
    lines, fetched = fetch(plants, cfg, session)
    df = compute(plants, lines, cfg, fetched)
    r = cfg["layers"][NAME]["search_radius_mi"]
    log.info("gas pipelines: %d plants; gas transmission within %s mi for %d, any gas line for %d; median %.1f mi",
             len(df), r, int(df["dist_gas_transmission_mi"].notna().sum()), int(df["dist_gas_any_mi"].notna().sum()),
             float(df["dist_gas_transmission_mi"].median()) if df["dist_gas_transmission_mi"].notna().any() else float("nan"))
    return g.write_layer(NAME, df)
