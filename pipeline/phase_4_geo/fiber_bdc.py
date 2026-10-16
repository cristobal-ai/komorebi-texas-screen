"""Layer 10: FCC Broadband Data Collection (BDC) fiber-to-the-premises availability around each plant.

The fiber PROXY layer measures where long-haul fiber usually runs; this one adds where a provider has reported FTTP service
(technology code 50) to real locations, which is the closest public evidence that a lateral to the site is short and that a
carrier is active nearby. Display only (no points): FTTP availability is last-mile, not a route to a carrier hotel.

Input (manual, the FCC download needs a login and has no stable URL): from broadbandmap.fcc.gov -> Data Download -> Fixed
Broadband Availability -> Texas -> *Fiber to the Premises* (CSV, or the whole state with all technologies; rows of other
technologies are ignored), dropped in data/raw/fiber_bdc/manual/. Columns used: h3_res8_id, provider_id, brand_name, technology,
max_advertised_download_speed, max_advertised_upload_speed, business_residential_code, location_id.

The CSV is reduced once to data/raw/fiber_bdc/h3_fiber.parquet (one row per H3 res-8 cell x provider; delete it to re-read after
a new release). Per plant (distances edge-of-array to cell centre, so +/- one hexagon edge, ~0.3 mi):
    bdc_status               ok | no_bdc_data (never reached: the run stops without a file)
    bdc_fiber_at_site        an FTTP cell centre lies within site_cell_m of the array
    bdc_fiber_nearest_mi     nearest FTTP cell (null beyond search_radius_mi); bdc_nearest_brands = providers in that cell
    bdc_providers_near       distinct providers within near_miles; bdc_brands_near = top providers by cells
    bdc_cells_near           FTTP cells within near_miles (a density measure)
    bdc_max_down_mbps_near / bdc_max_up_mbps_near
    bdc_business_fiber_near  any provider reports business (B/X) FTTP service within near_miles
    bdc_release, bdc_source, bdc_fetched, bdc_confidence (medium; low for plants located by an EIA point)
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from pipeline.common import load_config, raw_dir
from pipeline.phase_4_geo import common as g

log = logging.getLogger(__name__)
NAME = "fiber_bdc"
COLS = {"h3": "h3_res8_id", "provider": "provider_id", "brand": "brand_name", "tech": "technology",
        "down": "max_advertised_download_speed", "up": "max_advertised_upload_speed",
        "bus": "business_residential_code", "loc": "location_id"}


def _h3():
    try:
        import h3
    except ImportError as e:                                          # pragma: no cover
        raise SystemExit("pip install h3   (needed to place FCC BDC hexagons)") from e
    return h3


def find_inputs(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*") if p.suffix.lower() in (".csv", ".zip"))


def release_of(name: str) -> str | None:
    """'bdc_48_Fiber-to-the-Premises_fixed_broadband_J25_30jun2026.csv' -> 'J25 30jun2026'"""
    m = re.search(r"_([A-Z]\d{2})_(\d{1,2}[a-z]{3}\d{4})", name)
    return f"{m.group(1)} {m.group(2)}" if m else None


def reduce_chunk(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """One CSV chunk -> rows per (cell, provider): locations, best speeds, business flag. Other technologies are dropped."""
    c = cfg["layers"][NAME]
    missing = [v for k, v in COLS.items() if k != "tech" and v not in df.columns]
    if missing:
        raise SystemExit(f"FCC BDC file lacks columns {missing}; found {list(df.columns)}")
    if COLS["tech"] in df.columns:
        df = df[pd.to_numeric(df[COLS["tech"]], errors="coerce") == c["technology_code"]]
    if df.empty:
        return pd.DataFrame(columns=["h3", "provider_id", "brand", "locations", "max_down", "max_up", "business"])
    d = pd.DataFrame({
        "h3": df[COLS["h3"]].astype(str).str.strip().str.lower(), "provider_id": df[COLS["provider"]],
        "brand": df[COLS["brand"]].astype(str).str.strip(), "loc": df[COLS["loc"]],
        "down": pd.to_numeric(df[COLS["down"]], errors="coerce"), "up": pd.to_numeric(df[COLS["up"]], errors="coerce"),
        "business": df[COLS["bus"]].astype(str).str.strip().str.upper().isin(c["business_codes"])})
    return (d.groupby(["h3", "provider_id", "brand"], as_index=False)
            .agg(locations=("loc", "size"), max_down=("down", "max"), max_up=("up", "max"), business=("business", "max")))


def merge_reduced(parts: list[pd.DataFrame]) -> pd.DataFrame:
    d = pd.concat(parts, ignore_index=True)
    return (d.groupby(["h3", "provider_id", "brand"], as_index=False)
            .agg(locations=("locations", "sum"), max_down=("max_down", "max"), max_up=("max_up", "max"), business=("business", "max")))


def read_inputs(files: list[Path], cfg: dict) -> pd.DataFrame:
    parts = []
    for f in files:
        log.info("reading %s", f.name)
        opener = zipfile.ZipFile(f) if f.suffix.lower() == ".zip" else None
        members = [n for n in opener.namelist() if n.lower().endswith(".csv")] if opener else [None]
        for m in members:
            src = opener.open(m) if opener else f
            for chunk in pd.read_csv(src, chunksize=cfg["layers"][NAME]["chunk_rows"], dtype=str):
                parts.append(reduce_chunk(chunk, cfg))
    d = merge_reduced([p for p in parts if not p.empty]) if any(not p.empty for p in parts) else None
    if d is None:
        raise SystemExit(f"no technology-{cfg['layers'][NAME]['technology_code']} rows in {[f.name for f in files]}")
    return d


def load_cells(cfg: dict) -> tuple[pd.DataFrame, str, str | None, str]:
    """(cell x provider table, source file names, release, fetched date). Cached; delete the parquet to re-read."""
    folder = raw_dir(NAME) / "manual"
    folder.mkdir(exist_ok=True)
    cache = raw_dir(NAME) / "h3_fiber.parquet"
    files = find_inputs(folder)
    if not files and not cache.exists():
        raise SystemExit(
            f"No FCC BDC file in {folder}.\nDownload Texas 'Fiber to the Premises' availability (CSV) from "
            "https://broadbandmap.fcc.gov/data-download/nationwide-data (Fixed Broadband Availability -> Texas) and drop it there.")
    newest = max((f.stat().st_mtime for f in files), default=0)
    if files and (not cache.exists() or cache.stat().st_mtime < newest):
        d = read_inputs(files, cfg)
        d.to_parquet(cache)
        meta = {"source": "; ".join(f.name for f in files), "release": next((release_of(f.name) for f in files if release_of(f.name)), None)}
        pd.Series(meta).to_json(cache.with_suffix(".json"))
        log.info("FCC BDC: %d cell x provider rows, %d cells, %d providers", len(d), d["h3"].nunique(), d["provider_id"].nunique())
    meta = pd.read_json(cache.with_suffix(".json"), typ="series") if cache.with_suffix(".json").exists() else pd.Series({"source": "cache"})
    fetched = dt.datetime.fromtimestamp(cache.stat().st_mtime).date().isoformat()
    return pd.read_parquet(cache), str(meta.get("source")), meta.get("release"), fetched


def cell_points(cells: pd.DataFrame) -> gpd.GeoDataFrame:
    """One point (cell centre) per H3 cell, with the providers serving it."""
    h3 = _h3()
    by_cell = cells.groupby("h3").agg(providers=("provider_id", "nunique"), locations=("locations", "max"),
                                     max_down=("max_down", "max"), max_up=("max_up", "max"), business=("business", "max"),
                                     brands=("brand", lambda s: "; ".join(sorted(set(s))))).reset_index()
    ll = [h3.cell_to_latlng(c) for c in by_cell["h3"]]
    return gpd.GeoDataFrame(by_cell, geometry=[Point(lon, lat) for lat, lon in ll], crs="EPSG:4326")


def compute(plants: gpd.GeoDataFrame, cells: pd.DataFrame, cfg: dict, source: str, release: str | None, fetched: str) -> pd.DataFrame:
    c = cfg["layers"][NAME]
    pts = cell_points(cells)
    P5 = pts.to_crs(g.METRIC_CRS)
    prov_by_cell = cells.groupby("h3")["provider_id"].apply(set)
    brand_n = cells.groupby("brand")["h3"].nunique()
    near = g.within(plants, P5, c["near_miles"])
    nearest = g.nearest(plants, pts, ["h3", "brands"], "bdc")
    rows = []
    for i, pid in enumerate(plants["eia_id"].astype(int)):
        idx = near[pid]
        sub = pts.iloc[idx]
        provs = set().union(*[prov_by_cell[h] for h in sub["h3"]]) if len(sub) else set()
        cell_ids = set(sub["h3"])
        brands = (cells[cells["h3"].isin(cell_ids)].groupby("brand")["h3"].nunique().sort_values(ascending=False).head(c["top_brands"]).index
                  if len(sub) else [])
        rows.append({"eia_id": pid, "bdc_providers_near": len(provs), "bdc_cells_near": len(sub),
                     "bdc_brands_near": "; ".join(brands) or None,
                     "bdc_max_down_mbps_near": sub["max_down"].max() if len(sub) else np.nan,
                     "bdc_max_up_mbps_near": sub["max_up"].max() if len(sub) else np.nan,
                     "bdc_business_fiber_near": bool(sub["business"].any()) if len(sub) else False})
    out = pd.DataFrame(rows).merge(nearest[["eia_id", "bdc_mi", "bdc_brands"]].rename(
        columns={"bdc_mi": "bdc_fiber_nearest_mi", "bdc_brands": "bdc_nearest_brands"}), on="eia_id")
    far = out["bdc_fiber_nearest_mi"] > c["search_radius_mi"]
    out.loc[far, ["bdc_fiber_nearest_mi", "bdc_nearest_brands"]] = [np.nan, None]
    out["bdc_fiber_at_site"] = out["bdc_fiber_nearest_mi"] * g.M_PER_MI <= c["site_cell_m"]
    out["bdc_status"] = "ok"
    out["bdc_search_mi"] = c["search_radius_mi"]
    out["bdc_release"], out["bdc_source"], out["bdc_fetched"] = release, source, fetched
    out["bdc_confidence"] = np.where(plants["geometry_is_point"].to_numpy(), "low", "medium")
    return out


def run() -> Path:
    cfg = load_config()
    plants = g.load_plants()
    cells, source, release, fetched = load_cells(cfg)
    df = compute(plants, cells, cfg, source, release, fetched)
    log.info("fcc bdc fiber (%s): FTTP at the site for %d of %d plants; nearest median %.1f mi (max %.1f); provider within %s mi for %d",
             release or "release unknown", int(df["bdc_fiber_at_site"].sum()), len(df), df["bdc_fiber_nearest_mi"].median(),
             df["bdc_fiber_nearest_mi"].max(), cfg["layers"][NAME]["near_miles"], int((df["bdc_providers_near"] > 0).sum()))
    return g.write_layer(NAME, df)
