"""Layer 5: fiber PROXY. Distance to the corridors long-haul fiber is laid in, and to the Texas carrier hotels.

Long-haul routes are proprietary (plan §2 item 7), so this measures where fiber usually is, not where it is:
  * Class I railroad mainline (UP, BNSF, CPKC) from the BTS NTAD North American Rail Network, one Texas-wide query
    cached as data/raw/fiber/rail_class1.parquet;
  * interstate right-of-way (OSM highway=motorway/trunk with an "I nn" ref) from the Geofabrik Texas extract the
    transmission layer already downloaded, parsed once into data/raw/fiber/osm_interstates.parquet;
  * great-circle miles to the carrier hotels in config, × route_factor → an estimated route and round-trip latency.
FCC BDC fiber-to-the-premises availability at the site (also in the plan) is not read yet.

Output data/layers/fiber.parquet, one row per eia_id:
    dist_class1_rail_mi, nearest_rail_owner, nearest_rail_subdiv
    dist_interstate_mi, nearest_interstate
    fiber_lateral_miles     min of the two corridor distances: the lateral the fixed-cost drag (Section F) prices
    fiber_corridor          rail | interstate (whichever is nearer)
    nearest_carrier_hotel, carrier_hotel_gc_mi, carrier_hotel_route_mi_est, latency_rtt_ms_est
    carrier_hotel_rtt_ms    every configured hotel: 'name ~x.x ms' joined by '; '
    fiber_search_mi, fiber_source, fiber_fetched, fiber_confidence (always low: a proxy)
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import LineString

from pipeline.common import download, load_config, raw_dir
from pipeline.phase_4_geo import arcgis
from pipeline.phase_4_geo import common as g

log = logging.getLogger(__name__)
NAME = "fiber"
RAIL_FIELDS = ["OBJECTID", "RROWNER1", "SUBDIV", "NET", "STATEAB"]
EARTH_MI = 3958.8


# ---- sources ----------------------------------------------------------------------------------------------------------
def rail_where(c: dict) -> str:
    owners = ",".join(f"'{o}'" for o in c["rail_owners"])
    nets = ",".join(f"'{n}'" for n in c["rail_net"])
    return f"RROWNER1 IN ({owners}) AND NET IN ({nets})"


def fetch_rail(cfg: dict, session=requests, sleep=time.sleep) -> tuple[gpd.GeoDataFrame, str]:
    c = cfg["layers"][NAME]
    p = raw_dir(NAME) / "rail_class1.parquet"
    if not p.exists():
        rail = arcgis.query(c["rail_layer_url"], rail_where(c), RAIL_FIELDS, tuple(c["rail_bbox"]), page=2000,
                            max_pages=60, session=session, sleep=sleep)
        rail.to_parquet(p)
        log.info("NTAD rail: %d Class I mainline segments (%s)", len(rail), ", ".join(c["rail_owners"]))
    return gpd.read_parquet(p), dt.datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()


def interstate_ref(ref, pattern: str) -> str | None:
    """'I 10;US 290' → 'I-10'; 'US 287' → None."""
    if not ref:
        return None
    m = re.search(pattern, str(ref))
    if not m:
        return None
    num = re.search(r"\d+", str(ref)[m.start():]).group(0)
    return f"I-{num}"


def read_interstates(pbf: Path, pattern: str) -> gpd.GeoDataFrame:
    """One pass over the OSM extract: motorway/trunk ways whose ref names an interstate. Needs pyosmium."""
    import osmium

    rows: list[dict] = []

    class H(osmium.SimpleHandler):
        def way(self, w):
            if w.tags.get("highway") not in ("motorway", "trunk"):
                return
            ref = interstate_ref(w.tags.get("ref"), pattern)
            if not ref:
                return
            try:
                pts = [(nd.lon, nd.lat) for nd in w.nodes if nd.location.valid()]
            except osmium.InvalidLocationError:
                return
            if len(pts) >= 2:
                rows.append({"osm_id": f"w{w.id}", "ref": ref, "geometry": LineString(pts)})

    H().apply_file(str(pbf), locations=True)
    if not rows:
        return arcgis.empty(["osm_id", "ref"])
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


def fetch_interstates(cfg: dict) -> tuple[gpd.GeoDataFrame, str]:
    p = raw_dir(NAME) / "osm_interstates.parquet"
    if not p.exists():
        url = cfg["layers"]["transmission"]["pbf_url"]
        pbf = raw_dir("transmission") / Path(url).name
        if not pbf.exists():
            log.info("downloading %s (~700 MB, shared with the transmission layer)", url)
            download(url, pbf, timeout=(30, 120))
        log.info("reading %s for interstate ways (1-3 minutes)", pbf.name)
        lines = read_interstates(pbf, cfg["layers"][NAME]["interstate_ref_regex"])
        lines.to_parquet(p)
        log.info("OSM: %d interstate way segments (%d routes)", len(lines), lines["ref"].nunique() if len(lines) else 0)
    return gpd.read_parquet(p), dt.datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()


# ---- compute ----------------------------------------------------------------------------------------------------------
def great_circle_mi(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi, dlmb = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_MI * np.arcsin(np.sqrt(a))


def carrier_hotels(plants: gpd.GeoDataFrame, c: dict) -> pd.DataFrame:
    """Nearest configured carrier hotel by great circle (from the plant's EIA point), estimated route and RTT."""
    hotels = c["carrier_hotels"]
    lat, lon = plants["lat"].to_numpy(float), plants["lon"].to_numpy(float)
    gc = np.column_stack([great_circle_mi(lat, lon, h["lat"], h["lon"]) for h in hotels])   # plants × hotels
    rtt = gc * c["route_factor"] * c["rtt_ms_per_route_mile"]
    best = np.nanargmin(np.where(np.isnan(gc), np.inf, gc), axis=1)
    rows = np.arange(len(plants))
    out = pd.DataFrame({
        "eia_id": plants["eia_id"].to_numpy(),
        "nearest_carrier_hotel": [hotels[i]["name"] for i in best],
        "carrier_hotel_gc_mi": gc[rows, best],
        "carrier_hotel_route_mi_est": gc[rows, best] * c["route_factor"],
        "latency_rtt_ms_est": rtt[rows, best],
        "carrier_hotel_rtt_ms": ["; ".join(f"{h['name'].split(' (')[0]} ~{rtt[r, i]:.1f} ms" for i, h in enumerate(hotels)) for r in rows],
    })
    bad = np.isnan(lat) | np.isnan(lon)
    out.loc[bad, ["nearest_carrier_hotel", "carrier_hotel_gc_mi", "carrier_hotel_route_mi_est", "latency_rtt_ms_est",
                  "carrier_hotel_rtt_ms"]] = None
    return out


def compute(plants: gpd.GeoDataFrame, rail: gpd.GeoDataFrame, interstates: gpd.GeoDataFrame, cfg: dict,
            fetched: str) -> pd.DataFrame:
    c = cfg["layers"][NAME]
    out = g.nearest(plants, rail, ["RROWNER1", "SUBDIV"], "rail").rename(columns={
        "rail_mi": "dist_class1_rail_mi", "rail_RROWNER1": "nearest_rail_owner", "rail_SUBDIV": "nearest_rail_subdiv"})
    out = out.merge(g.nearest(plants, interstates, ["ref"], "hwy").rename(columns={
        "hwy_mi": "dist_interstate_mi", "hwy_ref": "nearest_interstate"}), on="eia_id")
    r = c["search_radius_mi"]
    for dist, attrs in (("dist_class1_rail_mi", ["nearest_rail_owner", "nearest_rail_subdiv"]),
                        ("dist_interstate_mi", ["nearest_interstate"])):
        far = out[dist] > r
        out.loc[far, dist] = np.nan
        for a in attrs:
            out[a] = out[a].astype(object)
            out.loc[out[dist].isna(), a] = None
    both = out[["dist_class1_rail_mi", "dist_interstate_mi"]]
    out["fiber_lateral_miles"] = both.min(axis=1, skipna=True)
    out["fiber_corridor"] = np.where(both.isna().all(axis=1), None,
                                     np.where(both["dist_class1_rail_mi"].fillna(np.inf) <= both["dist_interstate_mi"].fillna(np.inf),
                                              "rail", "interstate"))
    out = out.merge(carrier_hotels(plants, c), on="eia_id", how="left")
    out["fiber_search_mi"] = r
    out["fiber_source"] = "proxy: ntad class I mainline + osm interstates; peeringdb carrier hotels"
    out["fiber_fetched"] = fetched
    out["fiber_confidence"] = "low"
    return out


def run(session=requests) -> Path:
    cfg = load_config()
    plants = g.load_plants()
    rail, f1 = fetch_rail(cfg, session)
    hwy, f2 = fetch_interstates(cfg)
    df = compute(plants, rail, hwy, cfg, min(f1, f2))
    log.info("fiber proxy: %d plants; lateral median %.1f mi (max %.1f); nearest corridor rail %d / interstate %d; "
             "carrier-hotel RTT median %.1f ms", len(df), df["fiber_lateral_miles"].median(), df["fiber_lateral_miles"].max(),
             int((df["fiber_corridor"] == "rail").sum()), int((df["fiber_corridor"] == "interstate").sum()),
             df["latency_rtt_ms_est"].median())
    return g.write_layer(NAME, df)
