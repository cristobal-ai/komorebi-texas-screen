"""Layer 6: climate. Hours below the dry-bulb thresholds that matter for a dry-cooler / ground-coupled cooling plant.

Source: NSRDB PSM v4 (NLR, formerly NREL) single-point CSV, hourly air temperature and relative humidity at one GOES
cell (~4 km) per plant: the array's representative point, else the EIA lat/lon. Requests use the point rounded to
`point_round_deg`, so neighbouring plants share a download. Every year in `years` plus the TMY is cached under
data/raw/climate/hourly/<dataset>_<name>_<lat>_<lon>.parquet (delete to refresh). NSRDB air temperature is MERRA-2
reanalysis downscaled to the cell: a modelled value, climate_confidence = medium (low when the plant has no polygon).

Hour counts are normalised to 8,760 h (share of the year's hours × 8,760) so leap years do not count extra.

Output data/layers/climate.parquet, one row per eia_id:
    hours_below_25c_drybulb       mean over `years` (scored, Section E)
    hours_below_25c_drybulb_min   worst year
    hours_below_25c_drybulb_tmy   typical meteorological year
    hours_below_25c_by_year       'yyyy: n; ...'
    hours_below_15c_drybulb, hours_below_20c_drybulb, hours_below_20c_wetbulb, hours_above_35c_drybulb   (means)
    mean_annual_temp_c            ~ undisturbed ground temperature below ~10 m: the ground-loop starting point
    design_drybulb_0p4_c, design_wetbulb_0p4_c   0.4% annual exceedance (99.6th percentile of the hourly values)
    max_drybulb_c
    nsrdb_location_id, nsrdb_lat, nsrdb_lon, nsrdb_elevation_m, climate_years
    climate_source, climate_fetched, climate_confidence
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

from pipeline.common import load_config, load_env, raw_dir
from pipeline.phase_4_geo import common as g

log = logging.getLogger(__name__)
NAME = "climate"
HOURS_PER_YEAR = 8760


# ---- source -----------------------------------------------------------------------------------------------------------
def request_point(lat: float, lon: float, step: float) -> tuple[float, float]:
    r = lambda v: round(round(v / step) * step, 4)
    return r(lat), r(lon)


def plant_points(plants: gpd.GeoDataFrame) -> pd.DataFrame:
    """eia_id, lat, lon of the array's representative point (inside the polygon), else the EIA point."""
    pts = plants.to_crs("EPSG:4326").geometry.representative_point()
    return pd.DataFrame({"eia_id": plants["eia_id"].to_numpy(), "lat": pts.y.to_numpy(), "lon": pts.x.to_numpy()})


def parse_csv(text: str) -> pd.DataFrame:
    """NSRDB CSV: line 1 metadata names, line 2 metadata values, then the hourly table. Metadata ride along as columns."""
    lines = text.splitlines()
    if len(lines) < 4 or not lines[0].startswith("Source"):
        raise ValueError(f"not an NSRDB CSV: {text[:300]!r}")
    meta = dict(zip(lines[0].split(","), lines[1].split(",")))
    df = pd.read_csv(io.StringIO("\n".join(lines[2:])))
    out = pd.DataFrame({"year": df["Year"], "month": df["Month"], "temp_c": df["Temperature"].astype(float),
                        "rh_pct": df["Relative Humidity"].astype(float)})
    out["location_id"] = int(meta["Location ID"])
    out["cell_lat"], out["cell_lon"] = float(meta["Latitude"]), float(meta["Longitude"])
    out["elevation_m"] = float(meta["Elevation"])
    return out


def fetch(lat: float, lon: float, dataset: str, name: str, cfg: dict, session=requests, sleep=time.sleep) -> pd.DataFrame:
    c = cfg["layers"][NAME]
    p = raw_dir(NAME) / "hourly" / f"{dataset}_{name}_{lat:.2f}_{lon:.2f}.parquet"
    if p.exists():
        return pd.read_parquet(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    key, email = os.environ.get("NLR_API_KEY", "").strip(), os.environ.get("NLR_API_EMAIL", "").strip()
    if not key or not email:
        raise SystemExit("NLR_API_KEY and NLR_API_EMAIL must be set (pipeline/.env or environment)")
    params = {"api_key": key, "email": email, "wkt": f"POINT({lon} {lat})", "names": name,
              "interval": c["interval_min"], "attributes": ",".join(c["attributes"]), "utc": "false", "leap_day": "true"}
    url = c["api_url"].format(dataset=dataset)
    attempt, throttled = 0, 0
    while attempt < c["retries"]:
        try:
            r = session.get(url, params=params, timeout=(20, 180))
        except requests.RequestException as e:
            err = f"{type(e).__name__}"
        else:
            if r.status_code == 429 and throttled < c["max_rate_limit_waits"]:
                # OVER_RATE_LIMIT: a short-window cap shared by the parallel workers; wait it out without using a retry
                throttled += 1
                sleep(c["rate_limit_wait_s"] * throttled)
                continue
            if r.status_code == 200 and r.text.startswith("Source"):
                df = parse_csv(r.text)
                tmp = p.with_suffix(".part")
                df.to_parquet(tmp, index=False)
                tmp.replace(p)
                sleep(c["pause_s"])
                return df
            err = f"HTTP {r.status_code} {r.text[:300]}"
            # bad request / key: retrying will not help. NLR also answers a transient server-side "Data processing
            # failure" with 400 (seen 5 Oct 2026; the same request succeeded minutes later): that one is retried.
            transient = r.status_code == 400 and "processing fail" in r.text.lower()
            if r.status_code in (400, 401, 403, 404) and not transient:
                raise RuntimeError(f"NSRDB {dataset} {name} at ({lat}, {lon}): {err}")
        attempt += 1
        wait = min(60, 5 * 2 ** (attempt - 1))
        log.warning("NSRDB %s %s at (%s, %s): %s; retry %d in %ds", dataset, name, lat, lon, err, attempt, wait)
        sleep(wait)
    raise RuntimeError(f"NSRDB {dataset} {name} at ({lat}, {lon}) failed after {c['retries']} attempts")


# ---- compute ----------------------------------------------------------------------------------------------------------
def wet_bulb_stull(t_c, rh_pct):
    """Stull (2011) wet-bulb at sea-level pressure; within ~0.3 C for RH 5-99 % and -20..50 C. RH is clipped to that range."""
    t = np.asarray(t_c, float)
    rh = np.clip(np.asarray(rh_pct, float), 5, 99)
    return (t * np.arctan(0.151977 * np.sqrt(rh + 8.313659)) + np.arctan(t + rh) - np.arctan(rh - 1.676331)
            + 0.00391838 * rh ** 1.5 * np.arctan(0.023101 * rh) - 4.686035)


def hours(mask: pd.Series) -> float:
    """Share of valid hours where mask is true, × 8,760."""
    m = mask.dropna()
    return float(m.mean() * HOURS_PER_YEAR) if len(m) else np.nan


def year_stats(h: pd.DataFrame, c: dict) -> dict:
    t = h["temp_c"].where(h["temp_c"].notna())
    wb = pd.Series(wet_bulb_stull(h["temp_c"], h["rh_pct"]), index=h.index).where(t.notna() & h["rh_pct"].notna())
    out = {f"below_{x}c_drybulb": hours((t < x).where(t.notna())) for x in c["drybulb_below_c"]}
    out |= {f"below_{x}c_wetbulb": hours((wb < x).where(wb.notna())) for x in c["wetbulb_below_c"]}
    out[f"above_{c['drybulb_above_c']}c_drybulb"] = hours((t > c["drybulb_above_c"]).where(t.notna()))
    return out


def summarize(years: dict[int, pd.DataFrame], tmy: pd.DataFrame | None, c: dict) -> dict:
    """One plant: per-year hour counts averaged; percentiles and mean temperature over all years pooled."""
    per = {y: year_stats(h, c) for y, h in sorted(years.items())}
    keys = next(iter(per.values())).keys()
    row = {f"hours_{k}": round(float(np.mean([per[y][k] for y in per])), 0) for k in keys}
    b25 = {y: per[y]["below_25c_drybulb"] for y in per}
    row["hours_below_25c_drybulb_min"] = round(min(b25.values()), 0)
    row["hours_below_25c_by_year"] = "; ".join(f"{y}: {v:.0f}" for y, v in b25.items())
    row["hours_below_25c_drybulb_tmy"] = round(year_stats(tmy, c)["below_25c_drybulb"], 0) if tmy is not None else np.nan
    pooled = pd.concat(years.values(), ignore_index=True)
    wb = wet_bulb_stull(pooled["temp_c"], pooled["rh_pct"])
    q = c["design_percentile"]
    row |= {
        "mean_annual_temp_c": round(float(pooled["temp_c"].mean()), 2),
        "design_drybulb_0p4_c": round(float(pooled["temp_c"].quantile(q)), 1),
        "design_wetbulb_0p4_c": round(float(np.nanquantile(wb, q)), 1),
        "max_drybulb_c": round(float(pooled["temp_c"].max()), 1),
        "nsrdb_location_id": int(pooled["location_id"].iloc[0]),
        "nsrdb_lat": float(pooled["cell_lat"].iloc[0]),
        "nsrdb_lon": float(pooled["cell_lon"].iloc[0]),
        "nsrdb_elevation_m": float(pooled["elevation_m"].iloc[0]),
        "climate_years": f"{min(per)}-{max(per)}" if len(per) > 1 else str(min(per)),
    }
    return row


def run(session=requests) -> Path:
    load_env()
    cfg = load_config()
    c = cfg["layers"][NAME]
    plants = g.load_plants()
    pts = plant_points(plants)
    pts[["req_lat", "req_lon"]] = [request_point(la, lo, c["point_round_deg"]) for la, lo in zip(pts["lat"], pts["lon"])]
    uniq = pts[["req_lat", "req_lon"]].drop_duplicates()
    log.info("climate: %d plants on %d NSRDB request points; %d years + TMY each", len(pts), len(uniq), len(c["years"]))
    def one(la, lo):
        years = {int(y): fetch(la, lo, c["dataset"], str(y), cfg, session) for y in c["years"]}
        tmy = fetch(la, lo, c["tmy_dataset"], c["tmy_name"], cfg, session) if c.get("tmy_name") else None
        return summarize(years, tmy, c)

    summaries = {}
    with ThreadPoolExecutor(max_workers=c["workers"]) as ex:     # ~10 s per request; 1,000 requests/hour allowed
        futs = {ex.submit(one, la, lo): (la, lo) for la, lo in zip(uniq["req_lat"], uniq["req_lon"])}
        for i, f in enumerate(as_completed(futs), 1):
            summaries[futs[f]] = f.result()
            if i % 10 == 0 or i == len(futs):
                log.info("climate: %d / %d points", i, len(futs))
    rows = [{"eia_id": int(e)} | summaries[(la, lo)] for e, la, lo in zip(pts["eia_id"], pts["req_lat"], pts["req_lon"])]
    df = pd.DataFrame(rows)
    df["climate_source"] = f"nsrdb psm v4 ({c['dataset']}, {c['tmy_dataset']})"
    df["climate_fetched"] = dt.date.today().isoformat()
    df["climate_confidence"] = np.where(plants["geometry_is_point"].to_numpy(), "low", "medium")
    log.info("climate: hours below 25 C median %.0f (range %.0f-%.0f); mean annual temp median %.1f C; "
             "0.4%% design dry-bulb median %.1f C", df["hours_below_25c_drybulb"].median(), df["hours_below_25c_drybulb"].min(),
             df["hours_below_25c_drybulb"].max(), df["mean_annual_temp_c"].median(), df["design_drybulb_0p4_c"].median())
    return g.write_layer(NAME, df)
