"""Layer 4: flood. Share of each array in FEMA Special Flood Hazard Areas (1% annual chance), 0.2% zones and Zone D.

Source: FEMA NFHL MapServer, layer 28 (Flood Hazard Zones) clipped with the array polygon, and layer 0 (NFHL
Availability) to tell "no hazard zone here" from "no digital FIRM here". Cached per plant under data/raw/flood/plants/
(delete the folder to refresh). MapsMaker shows the same layer 28 as raster tiles.

Output data/layers/flood.parquet, one row per eia_id:
    flood_status        ok | not_mapped (no digital NFHL study covers the site: unknown, NOT "no risk")
    nfhl_mapped_share   share of the array inside a digital NFHL study area (< 1 on a mapped/unmapped county line)
    sfha_share          share of the whole array area in SFHA zones (A, AE, AH, AO, AR, A99, V, VE: SFHA_TF = 'T')
    sfha_acres
    floodway_share      share in a regulatory floodway (ZONE_SUBTY contains FLOODWAY)
    x500_share          share in the 0.2% annual-chance zone
    zone_d_share        share in Zone D (possible but undetermined hazard)
    flood_zones         ';'-joined FLD_ZONE values touching the array
    flood_flag          sfha_share >= flag_sfha_share (the brief's kill criterion; shown, not auto-failed). Null when not_mapped
    nfhl_study_ids      NFHL study (DFIRM) ids covering the site
    flood_source, flood_fetched, flood_confidence (medium; low when the plant has no polygon and a point buffer is used)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely import make_valid
from shapely.ops import unary_union

from pipeline.common import load_config, raw_dir
from pipeline.phase_4_geo import arcgis
from pipeline.phase_4_geo import common as g

log = logging.getLogger(__name__)
NAME = "flood"
M2_PER_ACRE = 4046.8564224
ZONE_FIELDS = ["OBJECTID", "FLD_ZONE", "ZONE_SUBTY", "SFHA_TF"]


def footprints(plants: gpd.GeoDataFrame, cfg: dict) -> gpd.GeoSeries:
    """Array polygons in metres; plants with only a point get a buffer (their result is low confidence)."""
    geo = plants.to_crs(g.METRIC_CRS).geometry
    buf = cfg["layers"][NAME]["point_buffer_m"]
    return gpd.GeoSeries([gm.buffer(buf) if pt else gm for gm, pt in zip(geo, plants["geometry_is_point"])],
                         index=plants.index, crs=g.METRIC_CRS)


def fetch_plant(eia_id: int, fp_4326, cfg: dict, session=requests, sleep=time.sleep) -> tuple[gpd.GeoDataFrame, dict]:
    """Zones and availability for one plant, cached as <eia_id>.parquet + <eia_id>.json."""
    c = cfg["layers"][NAME]
    d = raw_dir(NAME) / "plants"
    d.mkdir(exist_ok=True)
    pz, pj = d / f"{eia_id}.parquet", d / f"{eia_id}.json"
    if pz.exists() and pj.exists():
        return gpd.read_parquet(pz), json.loads(pj.read_text())
    base = c["service_url"].rstrip("/")
    bounds = tuple(fp_4326.bounds)
    # Study areas are county outlines: ask for them generalized (~10 m), enough to measure coverage of an array.
    avail = arcgis.query(f"{base}/{c['availability_layer']}", "1=1", ["OBJECTID", "STUDY_ID"], bounds,
                         max_offset=c["availability_offset_deg"], session=session, sleep=sleep)
    covered = unary_union([make_valid(gm) for gm in avail.geometry]).intersection(fp_4326) if len(avail) else None   # generalized outlines can self-intersect
    meta = {"study_ids": sorted({str(s) for s in avail["STUDY_ID"].dropna()}) if len(avail) else [],
            "mapped": bool(covered is not None and not covered.is_empty),
            "covered_wkt": covered.wkt if covered is not None and not covered.is_empty else None,
            "fetched": dt.date.today().isoformat()}
    zones = arcgis.query(f"{base}/{c['zones_layer']}", c["where"], ZONE_FIELDS, bounds, session=session, sleep=sleep) \
        if meta["mapped"] else arcgis.empty(ZONE_FIELDS)
    zones.to_parquet(pz)
    pj.write_text(json.dumps(meta))
    sleep(c["pause_s"])
    return zones, meta


def analyze(fp_m, zones: gpd.GeoDataFrame, meta: dict, cfg: dict) -> dict:
    """Clip zone polygons (EPSG:4326) with one footprint (metres) and report area shares."""
    c = cfg["layers"][NAME]
    row = {"flood_status": "ok" if meta.get("mapped") else "not_mapped",
           "nfhl_study_ids": ";".join(meta.get("study_ids") or []) or None, "flood_fetched": meta.get("fetched")}
    keys = ("nfhl_mapped_share", "sfha_share", "sfha_acres", "floodway_share", "x500_share", "zone_d_share")
    if not meta.get("mapped"):
        return row | {k: np.nan for k in keys} | {"flood_zones": None, "flood_flag": None}
    area = fp_m.area
    covered = gpd.GeoSeries.from_wkt([meta["covered_wkt"]], crs="EPSG:4326").to_crs(g.METRIC_CRS).iloc[0]
    row["nfhl_mapped_share"] = round(min(covered.intersection(fp_m).area / area, 1.0), 4) if area > 0 else np.nan
    z = zones.to_crs(g.METRIC_CRS) if len(zones) else zones
    if len(z):
        z = z[z.intersects(fp_m)].copy()
    if not len(z) or area <= 0:
        return row | {"sfha_share": 0.0, "sfha_acres": 0.0, "floodway_share": 0.0, "x500_share": 0.0, "zone_d_share": 0.0,
                      "flood_zones": None, "flood_flag": False}
    z["clip"] = z.geometry.apply(make_valid).intersection(fp_m)
    sub = z["ZONE_SUBTY"].fillna("").astype(str).str.upper()
    zone = z["FLD_ZONE"].fillna("").astype(str).str.upper().str.strip()

    def share(mask):
        geoms = [gm for gm in z.loc[mask, "clip"] if gm is not None and not gm.is_empty]
        return unary_union(geoms).area / area if geoms else 0.0   # union: overlapping zone polygons are not double-counted

    sfha_mask = z["SFHA_TF"].fillna("").astype(str).str.upper().eq("T")
    sfha = share(sfha_mask)
    out = {
        "sfha_share": round(sfha, 4),
        "sfha_acres": round(sfha * area / M2_PER_ACRE, 1),
        "floodway_share": round(share(sub.str.contains("FLOODWAY")), 4),
        "x500_share": round(share(~sfha_mask & sub.str.startswith("0.2 PCT")), 4),
        "zone_d_share": round(share(zone.eq("D")), 4),
        "flood_zones": ";".join(sorted(set(zone[zone != ""]))) or None,
        "flood_flag": bool(sfha >= c["flag_sfha_share"]),
    }
    return row | out


def run(session=requests) -> object:
    cfg = load_config()
    plants = g.load_plants()
    fps = footprints(plants, cfg)
    fps_4326 = fps.to_crs("EPSG:4326")
    rows = []
    for (idx, p), fp_m, fp_ll in zip(plants.iterrows(), fps, fps_4326):
        zones, meta = fetch_plant(int(p["eia_id"]), fp_ll, cfg, session)
        rows.append({"eia_id": int(p["eia_id"])} | analyze(fp_m, zones, meta, cfg))
    df = pd.DataFrame(rows)
    df["flood_source"] = "fema nfhl (layer 28)"
    df["flood_confidence"] = np.where(plants["geometry_is_point"].to_numpy(), "low", "medium")
    ok = df["flood_status"].eq("ok")
    log.info("flood: %d plants; %d with digital NFHL, %d not mapped; %d flagged (SFHA >= %.0f%% of the array); %d touch an SFHA",
             len(df), int(ok.sum()), int((~ok).sum()), int(df["flood_flag"].eq(True).sum()),
             100 * cfg["layers"][NAME]["flag_sfha_share"], int((df["sfha_share"] > 0).sum()))
    return g.write_layer(NAME, df)
