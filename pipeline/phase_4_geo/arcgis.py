"""ArcGIS REST feature queries (RRC pipelines, FEMA NFHL): paginated, retried, GeoJSON out.

The same endpoints MapsMaker reads in the browser through a CORS proxy; server-side no proxy is needed. FEMA's edge
resets TLS for the default python-requests User-Agent, so every call sends a browser-like one, and answers POST with an
empty 202, so queries are GETs.
"""
from __future__ import annotations

import logging
import time

import geopandas as gpd
import pandas as pd
import requests

log = logging.getLogger(__name__)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) komorebi-texas-screen/0.1"


def _get(url: str, params: dict, session, timeout: int, retries: int, sleep) -> dict:
    errors = []
    for attempt in range(retries):
        try:
            # GET, not POST: FEMA's edge answers POST with an empty 202. Envelope queries keep the URL short.
            r = session.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            if getattr(r, "status_code", 200) != 200:
                raise RuntimeError(f"HTTP {r.status_code} with no result")
            data = r.json()
            if isinstance(data, dict) and "error" in data:
                raise RuntimeError(f"ArcGIS error {data['error'].get('code')}: {data['error'].get('message')}")
            return data
        except Exception as e:                 # transient 5xx / reset / ArcGIS timeout: back off and retry
            errors.append(f"{type(e).__name__}: {e}")
            log.warning("ArcGIS query failed (%d/%d) %s: %s", attempt + 1, retries, url, e)
            if attempt < retries - 1:
                sleep(5 * (attempt + 1))
    raise RuntimeError(f"ArcGIS query failed after {retries} attempts: {url}\n  " + "\n  ".join(errors[-3:]))


def envelope(bounds: tuple[float, float, float, float]) -> dict:
    """(minx, miny, maxx, maxy) in EPSG:4326 → query params for an envelope intersect."""
    return {"geometry": ",".join(f"{v:.6f}" for v in bounds), "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "spatialRel": "esriSpatialRelIntersects"}


def query(layer_url: str, where: str, out_fields: list[str], bounds: tuple[float, float, float, float],
          page: int = 1000, max_pages: int = 50, timeout: int = 120, retries: int = 3, max_offset: float | None = None,
          session=requests, sleep=time.sleep) -> gpd.GeoDataFrame:
    """All features of `layer_url` (…/MapServer/<id>) intersecting `bounds`, as a GeoDataFrame in EPSG:4326.

    Pages with resultOffset ordered by OBJECTID (some servers repeat a page without an order). Raises if the result
    would exceed max_pages × page features rather than silently truncating. max_offset (degrees) asks the server to
    generalize geometry, for large outlines such as county study areas."""
    url = layer_url.rstrip("/") + "/query"
    feats = []
    for i in range(max_pages):
        params = envelope(bounds) | {
            "where": where, "outFields": ",".join(out_fields), "returnGeometry": "true", "outSR": "4326",
            "orderByFields": "OBJECTID", "resultOffset": str(i * page), "resultRecordCount": str(page), "f": "geojson",
        }
        if max_offset:
            params["maxAllowableOffset"] = str(max_offset)
        data = _get(url, params, session, timeout, retries, sleep)
        got = data.get("features", []) or []
        feats.extend(got)
        more = (data.get("properties") or {}).get("exceededTransferLimit") or data.get("exceededTransferLimit")
        if len(got) < page and not more:
            break
    else:
        raise RuntimeError(f"{url}: more than {max_pages * page} features in {bounds}; use smaller boxes")
    cols = {f: [] for f in out_fields}
    if not feats:
        return gpd.GeoDataFrame(cols, geometry=[], crs="EPSG:4326")
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    for f in out_fields:
        if f not in gdf.columns:
            gdf[f] = None
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    return gdf[[*out_fields, "geometry"]].reset_index(drop=True)


def count(layer_url: str, bounds: tuple[float, float, float, float], where: str = "1=1", timeout: int = 60,
          retries: int = 3, session=requests, sleep=time.sleep) -> int:
    params = envelope(bounds) | {"where": where, "returnCountOnly": "true", "f": "json"}
    return int(_get(layer_url.rstrip("/") + "/query", params, session, timeout, retries, sleep).get("count", 0))


def empty(fields: list[str]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(pd.DataFrame({f: [] for f in fields}), geometry=[], crs="EPSG:4326")
