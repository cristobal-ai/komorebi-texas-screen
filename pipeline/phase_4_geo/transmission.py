"""Layer 1: transmission. Distance from each plant to 345 kV substations and lines, and what voltage classes run nearby.

Source: OpenStreetMap (`power=line`, `power=substation`, voltage >= 100 kV): the Geofabrik Texas extract (parsed with pyosmium), or Overpass boxes around the plants as a fallback; or a HIFLD line file
dropped in data/raw/transmission/manual/ (columns VOLTAGE kV or VOLT_CLASS). Both are partial in places, so every row
carries `transmission_source`; the distance is a screen, not an interconnection study.

Output data/layers/transmission.parquet, one row per eia_id:
    dist_345kv_sub_mi      edge-to-edge miles to the nearest substation tagged >= 345 kV (the Section D scored distance)
    nearest_345kv_sub_name
    dist_345kv_line_mi     nearest line >= 345 kV (fallback when substations are untagged)
    dist_138kv_line_mi     nearest line >= 138 kV
    kv_classes_within_near voltage classes (kV, ';'-joined) of lines within near_miles
    max_kv_within_near     highest of those
    poi_kv_seen            True/False/None: does a line at the plant's EIA POI voltage run within near_miles
    transmission_search_mi  coverage radius: a missing distance means none within this many miles
    transmission_source, transmission_fetched, transmission_confidence
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import LineString, Point, Polygon

from pipeline.common import download, load_config, manual_file, raw_dir
from pipeline.phase_4_geo import common as g

log = logging.getLogger(__name__)
NAME = "transmission"
VOLT_RE = r"(^|;)[0-9]{6}($|;)"      # a 6-digit volts value (100-999 kV) anywhere in the tag


# ---- voltage parsing ------------------------------------------------------------------------------------------------
def parse_kv(tag) -> float:
    """Highest voltage in an OSM/HIFLD tag, in kV. '345000;138000' → 345; '138 kV' → 138; 138000 → 138; junk → NaN."""
    if tag is None or (isinstance(tag, float) and np.isnan(tag)):
        return np.nan
    vals = []
    for part in re.split(r"[;,/]", str(tag)):
        m = re.search(r"\d+(?:\.\d+)?", part.replace(" ", ""))
        if not m:
            continue
        v = float(m.group(0))
        if v <= 0 or v > 1_500_000:
            continue
        vals.append(v / 1000.0 if v >= 1000 else v)       # volts → kV; a bare 345 is already kV
    return max(vals) if vals else np.nan


def hifld_kv(row) -> float:
    """HIFLD: numeric VOLTAGE (kV, -999999 = unknown) else the top of VOLT_CLASS ('345', '220-287', '100-161')."""
    v = pd.to_numeric(row.get("voltage"), errors="coerce")
    if pd.notna(v) and v > 0:
        return float(v)
    vc = str(row.get("volt_class") or "")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", vc)]
    if not nums:
        return np.nan
    top = max(nums)
    return {345.0: 345.0, 287.0: 230.0, 161.0: 138.0, 100.0: 100.0}.get(top, top)


# ---- OpenStreetMap ----------------------------------------------------------------------------------------------------
def plant_bboxes(plants: gpd.GeoDataFrame, cfg: dict) -> list[tuple[float, float, float, float]]:
    """Unique (south, west, north, east) boxes covering every plant. A statewide Overpass query times out (504) on the
    public servers, so ask only for the neighbourhood of the plants: boxes are centred on a snap grid so plants in the
    same cell share one query."""
    c = cfg["layers"]["transmission"]
    snap, half = c["bbox_snap_deg"], c["bbox_half_deg"]
    boxes = set()
    for lat, lon in zip(plants["lat"], plants["lon"]):
        if pd.isna(lat) or pd.isna(lon):
            continue
        cy, cx = round(round(lat / snap) * snap, 4), round(round(lon / snap) * snap, 4)
        boxes.add((cy - half, cx - half, cy + half, cx + half))
    return sorted(boxes)


def overpass_queries(bbox: tuple[float, float, float, float], cfg: dict) -> tuple[str, str]:
    t = cfg["layers"]["transmission"]["overpass_timeout_s"]
    box = ",".join(f"{v:.4f}" for v in bbox)
    head = f"[out:json][timeout:{t}];"
    lines = head + f'(way["power"="line"]["voltage"~"{VOLT_RE}"]({box}););out geom tags;'
    subs = head + f'(nwr["power"="substation"]["voltage"~"{VOLT_RE}"]({box}););out center tags;'
    return lines, subs


def parse_overpass(payload: dict, kind: str) -> gpd.GeoDataFrame:
    """Overpass JSON → GeoDataFrame (EPSG:4326) with osm_id, name, voltage_kv. kind: 'line' or 'substation'."""
    rows, geoms = [], []
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        if kind == "line":
            pts = [(p["lon"], p["lat"]) for p in el.get("geometry", []) if "lat" in p]
            if el.get("type") != "way" or len(pts) < 2:
                continue
            geom = LineString(pts)
        else:
            if "lat" in el and "lon" in el:
                geom = Point(el["lon"], el["lat"])
            elif "center" in el:
                geom = Point(el["center"]["lon"], el["center"]["lat"])
            else:
                continue
        kv = parse_kv(tags.get("voltage"))
        if np.isnan(kv):
            continue
        rows.append({"osm_id": f"{el.get('type', '?')[0]}{el.get('id')}", "name": tags.get("name"),
                     "operator": tags.get("operator"), "voltage_kv": kv})
        geoms.append(geom)
    return gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326") if rows else gpd.GeoDataFrame(
        {"osm_id": [], "name": [], "operator": [], "voltage_kv": []}, geometry=[], crs="EPSG:4326")


def _overpass(query: str, cfg: dict, session=requests, sleep=time.sleep) -> dict:
    c = cfg["layers"]["transmission"]
    errors = []
    for attempt in range(3):                       # 3 rounds over the mirrors, 20 s / 40 s pause between rounds
        for url in c["overpass_urls"]:
            try:
                r = session.post(url, data={"data": query}, timeout=c["overpass_timeout_s"] + 60,
                                 headers={"User-Agent": "komorebi-texas-screen/0.1"})
                r.raise_for_status()
                return r.json()
            except Exception as e:                 # next mirror / next round
                errors.append(f"{url}: {e}")
                log.warning("Overpass failed %s: %s", url, e)
        if attempt < 2:
            sleep(20 * (attempt + 1))
    raise RuntimeError("all Overpass endpoints failed:\n  " + "\n  ".join(errors[-6:]))


def _box_key(b) -> str:
    return "_".join(f"{v:+.2f}".replace(".", "p") for v in b)


def fetch_osm(plants: gpd.GeoDataFrame, cfg: dict, session=requests,
              sleep=time.sleep) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, str]:
    """Lines and substations around the plants. Each box is cached under data/raw/transmission/osm/ and never
    re-fetched; adding plants in a new area fetches only the new boxes. Delete the folder to refresh."""
    d = raw_dir(NAME) / "osm"
    d.mkdir(exist_ok=True)
    boxes = plant_bboxes(plants, cfg)
    frames_l, frames_s = [], []
    pause = cfg["layers"]["transmission"]["pause_s"]
    for i, b in enumerate(boxes, 1):
        key = _box_key(b)
        lp, sp = d / f"lines_{key}.parquet", d / f"subs_{key}.parquet"
        if not (lp.exists() and sp.exists()):
            q_lines, q_subs = overpass_queries(b, cfg)
            lines = parse_overpass(_overpass(q_lines, cfg, session, sleep), "line")
            sleep(pause)
            subs = parse_overpass(_overpass(q_subs, cfg, session, sleep), "substation")
            lines.to_parquet(lp)
            subs.to_parquet(sp)
            log.info("Overpass box %d/%d %s: %d line segments, %d substations", i, len(boxes), b, len(lines), len(subs))
            sleep(pause)
        frames_l.append(gpd.read_parquet(lp))
        frames_s.append(gpd.read_parquet(sp))

    def merge(frames):
        frames = [f for f in frames if len(f)]
        if not frames:
            return gpd.GeoDataFrame({"osm_id": [], "name": [], "operator": [], "voltage_kv": []}, geometry=[], crs="EPSG:4326")
        out = pd.concat(frames, ignore_index=True)
        return gpd.GeoDataFrame(out.drop_duplicates("osm_id"), geometry="geometry", crs="EPSG:4326")

    stamp = max((dt.datetime.fromtimestamp(p.stat().st_mtime).date() for p in d.glob("*.parquet")),
                default=dt.date.today()).isoformat()
    return merge(frames_l), merge(frames_s), stamp


# ---- Geofabrik PBF (preferred) ----------------------------------------------------------------------------------------
def read_pbf(path: Path, min_kv: float) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """One pass over an OSM extract: power=line ways and power=substation nodes/ways with voltage >= min_kv.

    Substation ways (areas) are reduced to their centroid; multipolygon substation relations are skipped (a few per
    thousand). Needs the `osmium` package (pyosmium)."""
    import osmium

    class Handler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.lines: list[dict] = []
            self.subs: list[dict] = []

        def _rec(self, kind, el_id, tags, geom, kv):
            return {"osm_id": f"{kind}{el_id}", "name": tags.get("name"), "operator": tags.get("operator"),
                    "voltage_kv": kv, "geometry": geom}

        def node(self, n):
            if n.tags.get("power") != "substation":
                return
            kv = parse_kv(n.tags.get("voltage"))
            if np.isnan(kv) or kv < min_kv or not n.location.valid():
                return
            self.subs.append(self._rec("n", n.id, n.tags, Point(n.location.lon, n.location.lat), kv))

        def way(self, w):
            kind = w.tags.get("power")
            if kind not in ("line", "substation"):
                return
            kv = parse_kv(w.tags.get("voltage"))
            if np.isnan(kv) or kv < min_kv:
                return
            try:
                pts = [(nd.lon, nd.lat) for nd in w.nodes if nd.location.valid()]
            except osmium.InvalidLocationError:
                return
            if kind == "line" and len(pts) >= 2:
                self.lines.append(self._rec("w", w.id, w.tags, LineString(pts), kv))
            elif kind == "substation" and pts:
                geom = Polygon(pts).centroid if len(pts) >= 4 and pts[0] == pts[-1] else Point(np.mean(pts, axis=0))
                self.subs.append(self._rec("w", w.id, w.tags, geom, kv))

    h = Handler()
    h.apply_file(str(path), locations=True)

    def frame(rows):
        cols = ["osm_id", "name", "operator", "voltage_kv"]
        return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326") if rows else gpd.GeoDataFrame(
            {c: [] for c in cols}, geometry=[], crs="EPSG:4326")

    return frame(h.lines), frame(h.subs)


def fetch_pbf(cfg: dict) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, str]:
    """Download (once) and parse the Texas extract; the parsed lines/substations are cached as Parquet."""
    c = cfg["layers"]["transmission"]
    d = raw_dir(NAME)
    lp, sp = d / "pbf_lines.parquet", d / "pbf_substations.parquet"
    manual = manual_file(NAME, (".pbf",))
    pbf = manual or (d / Path(c["pbf_url"]).name)
    if lp.exists() and sp.exists():
        log.info("cache hit %s", lp)
        return gpd.read_parquet(lp), gpd.read_parquet(sp), dt.datetime.fromtimestamp(lp.stat().st_mtime).date().isoformat()
    if not pbf.exists():
        log.info("downloading %s (~700 MB, a few minutes)", c["pbf_url"])
        download(c["pbf_url"], pbf, timeout=(30, 120))
    log.info("reading %s for power lines and substations >= %s kV (1-3 minutes)", pbf.name, c["min_kv"])
    lines, subs = read_pbf(pbf, c["min_kv"])
    log.info("PBF: %d line segments, %d substations", len(lines), len(subs))
    lines.to_parquet(lp)
    subs.to_parquet(sp)
    return lines, subs, dt.datetime.fromtimestamp(pbf.stat().st_mtime).date().isoformat()


# ---- HIFLD override ---------------------------------------------------------------------------------------------------
def read_hifld(path: Path) -> gpd.GeoDataFrame:
    src = f"/vsizip/{path}" if path.suffix.lower() == ".zip" else str(path)
    gdf = gpd.read_file(src)
    gdf.columns = [c.lower() if c != "geometry" else c for c in gdf.columns]
    gdf["voltage_kv"] = gdf.apply(hifld_kv, axis=1)
    gdf = gdf[gdf["voltage_kv"].notna()].copy()
    gdf["name"] = gdf["owner"] if "owner" in gdf.columns else None
    gdf["osm_id"] = gdf["id"].astype(str) if "id" in gdf.columns else gdf.index.astype(str)
    return gdf[["osm_id", "name", "voltage_kv", gdf.geometry.name]].to_crs("EPSG:4326")


# ---- compute ----------------------------------------------------------------------------------------------------------
def compute(plants: gpd.GeoDataFrame, lines: gpd.GeoDataFrame, subs: gpd.GeoDataFrame, cfg: dict,
            source: str, fetched: str) -> pd.DataFrame:
    c = cfg["layers"]["transmission"]
    sub345 = subs[subs["voltage_kv"] >= 345] if len(subs) else subs
    line345 = lines[lines["voltage_kv"] >= 345] if len(lines) else lines
    line138 = lines[lines["voltage_kv"] >= 138] if len(lines) else lines

    out = g.nearest(plants, sub345, ["name"], "dist_345kv_sub").rename(columns={"dist_345kv_sub_name": "nearest_345kv_sub_name"})
    out = out.merge(g.nearest(plants, line345, [], "dist_345kv_line"), on="eia_id")
    out = out.merge(g.nearest(plants, line138, [], "dist_138kv_line"), on="eia_id")

    near = g.within(plants, lines, c["near_miles"])
    classes = c["kv_classes"]
    kv = lines["voltage_kv"].to_numpy() if len(lines) else np.array([])
    seen, maxes, eia_kv = [], [], dict(zip(plants["eia_id"], plants["grid_voltage_kv"]))
    for pid in out["eia_id"]:
        v = kv[near[int(pid)]] if len(kv) else np.array([])
        have = sorted({k for k in classes if np.any(np.abs(v - k) <= k * c["class_tolerance"])})
        seen.append(";".join(str(k) for k in have) or None)
        maxes.append(float(v.max()) if len(v) else np.nan)
    out["kv_classes_within_near"] = seen
    out["max_kv_within_near"] = maxes

    def poi_seen(pid, cls):
        poi = eia_kv.get(pid)
        if poi is None or pd.isna(poi):
            return None
        return bool(cls and any(abs(float(k) - poi) <= poi * c["class_tolerance"] for k in cls.split(";")))

    out["poi_kv_seen"] = [poi_seen(p, k) for p, k in zip(out["eia_id"], out["kv_classes_within_near"])]
    for col in ("dist_345kv_sub_mi", "dist_345kv_line_mi", "dist_138kv_line_mi"):
        out.loc[out[col] > c["search_radius_mi"], col] = np.nan      # beyond the covered radius the nearest feature may be missing
    out.loc[out["dist_345kv_sub_mi"].isna(), "nearest_345kv_sub_name"] = None
    out["transmission_search_mi"] = c["search_radius_mi"]      # a missing distance means "none within this radius"
    out["transmission_source"] = source
    out["transmission_fetched"] = fetched
    # OSM and HIFLD are crowd-sourced / aged: never 'measured'. Low where the plant had no polygon (centroid only).
    out["transmission_confidence"] = np.where(plants["geometry_is_point"].to_numpy(), "low", "medium")
    return out


def run(session=requests) -> Path:
    cfg = load_config()
    plants = g.load_plants()
    lines = subs = fetched = None
    source = "osm"
    if cfg["layers"]["transmission"]["use_pbf"]:
        try:
            lines, subs, fetched = fetch_pbf(cfg)
            source = "osm (geofabrik extract)"
        except Exception as e:                       # no osmium wheel, download blocked, corrupt file: use the API
            log.warning("Geofabrik extract unavailable (%s: %s); falling back to Overpass boxes", type(e).__name__, e)
    if lines is None:
        lines, subs, fetched = fetch_osm(plants, cfg, session)
        source = "osm (overpass)"
    manual = manual_file(NAME, (".zip", ".shp", ".geojson", ".json", ".gpkg"))
    if manual:
        log.info("using HIFLD lines from %s (substations stay OSM)", manual)
        lines, source = read_hifld(manual), "hifld lines + osm substations"
        fetched = dt.datetime.fromtimestamp(manual.stat().st_mtime).date().isoformat()
    df = compute(plants, lines, subs, cfg, source, fetched)
    n345 = int(df["dist_345kv_sub_mi"].notna().sum())
    log.info("transmission: %d plants; 345 kV substation found for %d; POI voltage seen nearby for %d of %d with an EIA POI",
             len(df), n345, int((df["poi_kv_seen"] == True).sum()), int(df["poi_kv_seen"].notna().sum()))
    return g.write_layer(NAME, df)
