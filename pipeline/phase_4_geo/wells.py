"""Layer 7: wells. Drillability and depth to water from Texas Water Development Board well records.

Sources (statewide nightly zips, cached under data/raw/wells/; delete a zip or its parquet to refresh):
  * Submitted Driller's Reports (SDRDownload.zip, wells since ~2001): location, total depth, the driller's lithology log
    (free text per depth interval), static water level, proposed use (incl. 'Closed-Loop Geothermal').
  * Groundwater Database (GWDBDownload.zip): monitored wells with aquifer and groundwater conservation district (GCD),
    and measured water levels (latest per well since `water_level_since`).

Drillability (brief §5 E, 4 pts): "absence of thick caliche/gypsum refusal layers per TWDB driller logs". For every
driller log near the plant, the feet of caliche / gypsum / anhydrite within the loop-bore depth (`bore_depth_ft`) are
summed; a log is "thick" at >= `thick_layer_ft`. Scored value = share of logs that are thick. Logs shallower than
`min_log_depth_ft` are left out. Driller descriptions are free text, so this is a screen, not a geotechnical finding.

Search: the array polygon (or the EIA point) outward to the first radius in `search_radii_mi` that holds at least
`min_logs` logs (resp. `min_levels` water levels); none within the largest radius = null (scored neutral: no data).

Output data/layers/wells.parquet, one row per eia_id:
    n_logs, logs_radius_mi, thick_hard_layer_share (scored), hard_layer_ft_median, hard_layer_ft_p90,
    caliche_log_share, gypsum_log_share, hard_rock_log_share, lost_circulation_log_share, median_log_depth_ft
    n_geothermal_bores           SDR wells with proposed use Closed-Loop Geothermal within the largest radius
    n_water_levels, water_radius_mi, depth_to_water_ft (median, scored), depth_to_water_ft_p25, _p75,
    water_level_sources ('sdr n; gwdb m'), latest_water_level_year
    aquifer_majority, gcd_majority   most common among GWDB wells within the largest radius (a proxy for the boundaries)
    wells_source, wells_fetched, wells_confidence (medium: >= min_logs within the first radius; low otherwise)
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

from pipeline.common import load_config, raw_dir
from pipeline.phase_4_geo import common as g

log = logging.getLogger(__name__)
NAME = "wells"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"}


# ---- sources ----------------------------------------------------------------------------------------------------------
def fetch_zip(url: str, session=requests) -> Path:
    p = raw_dir(NAME) / Path(url).name
    if p.exists() and p.stat().st_size > 0:
        return p
    log.info("GET %s", url)
    tmp = p.with_suffix(".part")
    with session.get(url, headers=UA, stream=True, timeout=(30, 300)) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    if open(tmp, "rb").read(4) != b"PK\x03\x04":
        tmp.unlink()
        raise ValueError(f"{url} is not a zip")
    tmp.replace(p)
    return p


def member(z: zipfile.ZipFile, name: str) -> str:
    """Zip member by file name (the zips nest a folder whose name may change)."""
    hits = [n for n in z.namelist() if n.split("/")[-1].lower() == name.lower() and "/readme/" not in n.lower()]
    if not hits:
        raise KeyError(f"{name} not in {z.filename}")
    return hits[0]


def read_table(z: zipfile.ZipFile, name: str, usecols: list[str]) -> pd.DataFrame:
    """Pipe-delimited TWDB table; rows with an extra '|' in a free-text field are skipped (counted in the log)."""
    with z.open(member(z, name)) as f:
        df = pd.read_csv(f, sep="|", usecols=usecols, dtype=str, encoding="latin-1", quoting=3, on_bad_lines="skip")
    return df


def read_lithology(z: zipfile.ZipFile) -> pd.DataFrame:
    """WellLithology: the description is the last column and may itself contain '|', so split on the first four."""
    ids, tops, bots, descs = [], [], [], []
    with z.open(member(z, "WellLithology.txt")) as f:
        text = io.TextIOWrapper(f, encoding="latin-1", newline="")
        next(text)
        for line in text:
            parts = line.rstrip("\r\n").split("|", 4)
            if len(parts) < 5:
                continue
            ids.append(parts[0]); tops.append(parts[2]); bots.append(parts[3]); descs.append(parts[4])
    df = pd.DataFrame({"id": pd.to_numeric(pd.Series(ids), errors="coerce"),
                       "top_ft": pd.to_numeric(pd.Series(tops), errors="coerce"),
                       "bottom_ft": pd.to_numeric(pd.Series(bots), errors="coerce"), "desc": descs})
    return df.dropna(subset=["id", "top_ft", "bottom_ft"]).astype({"id": "int64"})


def in_texas(lat: pd.Series, lon: pd.Series, bbox: list[float]) -> pd.Series:
    return lat.between(bbox[1], bbox[3]) & lon.between(bbox[0], bbox[2])


def load_sources(cfg: dict, session=requests) -> dict[str, pd.DataFrame]:
    """Parse both zips once into parquet under data/raw/wells/ (sdr_wells, sdr_lithology, sdr_levels, gwdb_wells, gwdb_levels)."""
    c = cfg["layers"][NAME]
    d = raw_dir(NAME)
    names = ["sdr_wells", "sdr_lithology", "sdr_levels", "gwdb_wells", "gwdb_levels"]
    if all((d / f"{n}.parquet").exists() for n in names):
        return {n: pd.read_parquet(d / f"{n}.parquet") for n in names}
    out: dict[str, pd.DataFrame] = {}
    bbox = c["texas_bbox"]

    z = zipfile.ZipFile(fetch_zip(c["sdr_url"], session))
    w = read_table(z, "WellData.txt", ["WellReportTrackingNumber", "County", "CoordDDLat", "CoordDDLong", "ProposedUse",
                                        "DrillingEndDate"])
    w = pd.DataFrame({"id": pd.to_numeric(w["WellReportTrackingNumber"], errors="coerce"), "county": w["County"],
                      "lat": pd.to_numeric(w["CoordDDLat"], errors="coerce"),
                      "lon": pd.to_numeric(w["CoordDDLong"], errors="coerce"), "use": w["ProposedUse"],
                      "year": pd.to_datetime(w["DrillingEndDate"], errors="coerce").dt.year})
    w["lon"] = -w["lon"].abs()                         # a few reports give west longitude as positive
    out["sdr_wells"] = w[w["id"].notna() & in_texas(w["lat"], w["lon"], bbox)].astype({"id": "int64"})
    out["sdr_lithology"] = read_lithology(z)
    lv = read_table(z, "WellLevels.txt", ["WellReportTrackingNumber", "Measurement", "MeasurementDate"])
    lv = pd.DataFrame({"id": pd.to_numeric(lv["WellReportTrackingNumber"], errors="coerce"),
                       "depth_ft": pd.to_numeric(lv["Measurement"], errors="coerce"),
                       "year": pd.to_datetime(lv["MeasurementDate"], errors="coerce").dt.year}).dropna(subset=["id", "depth_ft"])
    out["sdr_levels"] = lv.astype({"id": "int64"}).drop_duplicates("id")

    z = zipfile.ZipFile(fetch_zip(c["gwdb_url"], session))
    gm = read_table(z, "WellMain.txt", ["StateWellNumber", "County", "GCD", "Aquifer", "LatitudeDD", "LongitudeDD"])
    gm = pd.DataFrame({"swn": gm["StateWellNumber"], "county": gm["County"], "gcd": gm["GCD"], "aquifer": gm["Aquifer"],
                       "lat": pd.to_numeric(gm["LatitudeDD"], errors="coerce"),
                       "lon": -pd.to_numeric(gm["LongitudeDD"], errors="coerce").abs()})
    out["gwdb_wells"] = gm[in_texas(gm["lat"], gm["lon"], bbox)]
    frames = []
    for n in ("WaterLevelsMajor.txt", "WaterLevelsMinor.txt", "WaterLevelsOtherUnassigned.txt", "WaterLevelsCombination.txt"):
        x = read_table(z, n, ["StateWellNumber", "Status", "MeasurementYear", "MeasurementDate", "DepthFromLSD"])
        frames.append(x)
    x = pd.concat(frames, ignore_index=True)
    x = pd.DataFrame({"swn": x["StateWellNumber"], "status": x["Status"],
                      "year": pd.to_numeric(x["MeasurementYear"], errors="coerce"), "date": x["MeasurementDate"],
                      "depth_ft": pd.to_numeric(x["DepthFromLSD"], errors="coerce")})
    x = x[x["status"].fillna("").str.lower().eq("publishable") & x["depth_ft"].notna() & (x["year"] >= c["water_level_since"])]
    out["gwdb_levels"] = x.sort_values(["swn", "date"]).drop_duplicates("swn", keep="last")[["swn", "year", "depth_ft"]]

    for n, df in out.items():
        df.reset_index(drop=True).to_parquet(d / f"{n}.parquet", index=False)
        log.info("TWDB %s: %d rows", n, len(df))
    return out


# ---- per-log metrics ----------------------------------------------------------------------------------------------------
def log_metrics(lith: pd.DataFrame, c: dict) -> pd.DataFrame:
    """One row per driller log: deepest logged foot, feet of each class within the bore depth, lost circulation."""
    D = c["bore_depth_ft"]
    li = lith[lith["bottom_ft"] > lith["top_ft"]].copy()
    li["ft"] = (li["bottom_ft"].clip(upper=D) - li["top_ft"].clip(upper=D)).clip(lower=0)
    desc = li["desc"].fillna("").str.upper()
    pats = c["patterns"]
    li["caliche_ft"] = li["ft"].where(desc.str.contains(pats["caliche"], regex=True), 0.0)
    li["gypsum_ft"] = li["ft"].where(desc.str.contains(pats["gypsum"], regex=True), 0.0)
    li["hard_ft"] = li["ft"].where(desc.str.contains(f"{pats['caliche']}|{pats['gypsum']}", regex=True), 0.0)
    li["rock_ft"] = li["ft"].where(desc.str.contains(pats["hard_rock"], regex=True), 0.0)
    li["lost"] = desc.str.contains(pats["lost_circulation"], regex=True)
    m = li.groupby("id").agg(log_depth_ft=("bottom_ft", "max"), hard_ft=("hard_ft", "sum"), caliche_ft=("caliche_ft", "sum"),
                             gypsum_ft=("gypsum_ft", "sum"), rock_ft=("rock_ft", "sum"), lost=("lost", "any"))
    return m[m["log_depth_ft"] >= c["min_log_depth_ft"]].reset_index()


# ---- spatial ------------------------------------------------------------------------------------------------------------
def points(df: pd.DataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326").to_crs(g.METRIC_CRS)


def near(plants_m: gpd.GeoDataFrame, pts_m: gpd.GeoDataFrame, max_mi: float) -> pd.DataFrame:
    """(eia_id, row position in pts, miles) for every point within max_mi of a plant footprint (edge to edge)."""
    if pts_m.empty:
        return pd.DataFrame(columns=["eia_id", "pos", "mi"])
    tree = pts_m.sindex
    rows = []
    for pid, geom in zip(plants_m["eia_id"], plants_m.geometry):
        idx = tree.query(geom.buffer(max_mi * g.M_PER_MI), predicate="intersects")
        if len(idx):
            d = pts_m.geometry.iloc[idx].distance(geom).to_numpy() / g.M_PER_MI
            rows.append(pd.DataFrame({"eia_id": int(pid), "pos": idx, "mi": d}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["eia_id", "pos", "mi"])


def pick_radius(dists: np.ndarray, radii: list[float], need: int) -> float | None:
    for r in radii:
        if (dists <= r).sum() >= need:
            return r
    return None


def majority(s: pd.Series):
    s = s.dropna()
    s = s[s.str.strip().ne("")]
    return s.value_counts().index[0] if len(s) else None


def compute(plants: gpd.GeoDataFrame, src: dict[str, pd.DataFrame], cfg: dict, fetched: str) -> pd.DataFrame:
    c = cfg["layers"][NAME]
    radii, rmax = c["search_radii_mi"], max(c["search_radii_mi"])
    pm = plants[["eia_id", plants.geometry.name]].to_crs(g.METRIC_CRS)

    # driller logs: wells with a qualifying log, located
    wells = src["sdr_wells"]
    lm = log_metrics(src["sdr_lithology"][src["sdr_lithology"]["id"].isin(wells["id"])], c)
    logs = wells.merge(lm, on="id", how="inner").reset_index(drop=True)
    logs_m = points(logs)
    nl = near(pm, logs_m, rmax)
    geo = wells[wells["use"].fillna("").str.lower().eq("closed-loop geothermal")].reset_index(drop=True)
    ng = near(pm, points(geo), rmax)

    # water levels: SDR static level per report + GWDB latest measurement per monitored well
    sl = wells.merge(src["sdr_levels"], on="id", suffixes=("", "_lvl"))
    levels = pd.concat([pd.DataFrame({"lat": sl["lat"], "lon": sl["lon"], "depth_ft": sl["depth_ft"], "year": sl["year_lvl"],
                                      "src": "sdr"}),
                        src["gwdb_levels"].merge(src["gwdb_wells"], on="swn")[["lat", "lon", "depth_ft", "year"]].assign(src="gwdb")],
                       ignore_index=True)
    levels = levels[levels["depth_ft"] >= 0].reset_index(drop=True)     # flowing artesian (negative) left out
    nw = near(pm, points(levels), rmax)
    gw = src["gwdb_wells"].reset_index(drop=True)
    nm = near(pm, points(gw), rmax)

    rows = []
    q = lambda s, p: float(s.quantile(p)) if len(s) else np.nan
    for pid in plants["eia_id"].astype(int):
        row: dict = {"eia_id": pid}
        a = nl[nl["eia_id"] == pid]
        r = pick_radius(a["mi"].to_numpy(), radii, c["min_logs"])
        if r is not None:
            sel = logs.iloc[a.loc[a["mi"] <= r, "pos"].to_numpy()]
            row |= {"n_logs": len(sel), "logs_radius_mi": r,
                    "thick_hard_layer_share": round(float((sel["hard_ft"] >= c["thick_layer_ft"]).mean()), 3),
                    "hard_layer_ft_median": q(sel["hard_ft"], 0.5), "hard_layer_ft_p90": q(sel["hard_ft"], 0.9),
                    "caliche_log_share": round(float((sel["caliche_ft"] > 0).mean()), 3),
                    "gypsum_log_share": round(float((sel["gypsum_ft"] > 0).mean()), 3),
                    "hard_rock_log_share": round(float((sel["rock_ft"] >= c["thick_layer_ft"]).mean()), 3),
                    "lost_circulation_log_share": round(float(sel["lost"].mean()), 3),
                    "median_log_depth_ft": q(sel["log_depth_ft"], 0.5)}
        else:
            row |= {"n_logs": int(len(a)), "logs_radius_mi": None}
        row["n_geothermal_bores"] = int((ng["eia_id"] == pid).sum())
        b = nw[nw["eia_id"] == pid]
        r = pick_radius(b["mi"].to_numpy(), radii, c["min_levels"])
        if r is not None:
            sel = levels.iloc[b.loc[b["mi"] <= r, "pos"].to_numpy()]
            row |= {"n_water_levels": len(sel), "water_radius_mi": r, "depth_to_water_ft": q(sel["depth_ft"], 0.5),
                    "depth_to_water_ft_p25": q(sel["depth_ft"], 0.25), "depth_to_water_ft_p75": q(sel["depth_ft"], 0.75),
                    "water_level_sources": "; ".join(f"{k} {v}" for k, v in sel["src"].value_counts().sort_index().items()),
                    "latest_water_level_year": int(sel["year"].max()) if sel["year"].notna().any() else None}
        else:
            row |= {"n_water_levels": int(len(b)), "water_radius_mi": None}
        m = nm[nm["eia_id"] == pid]
        gsel = gw.iloc[m["pos"].to_numpy()] if len(m) else gw.iloc[[]]
        row |= {"aquifer_majority": majority(gsel["aquifer"]), "gcd_majority": majority(gsel["gcd"])}
        rows.append(row)

    df = pd.DataFrame(rows)
    for col in ("thick_hard_layer_share", "hard_layer_ft_median", "hard_layer_ft_p90", "caliche_log_share", "gypsum_log_share",
                "hard_rock_log_share", "lost_circulation_log_share", "median_log_depth_ft", "depth_to_water_ft",
                "depth_to_water_ft_p25", "depth_to_water_ft_p75", "logs_radius_mi", "water_radius_mi"):
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("water_level_sources",):
        if col not in df.columns:
            df[col] = None
    df["latest_water_level_year"] = pd.to_numeric(df.get("latest_water_level_year"), errors="coerce").astype("Int64")
    df["wells_source"] = "twdb sdr (driller logs, levels) + gwdb (levels, aquifer, gcd)"
    df["wells_fetched"] = fetched
    first = df["logs_radius_mi"].eq(radii[0])
    df["wells_confidence"] = np.where(first & ~plants["geometry_is_point"].to_numpy(), "medium", "low")
    return df


def run(session=requests) -> Path:
    cfg = load_config()
    plants = g.load_plants()
    src = load_sources(cfg, session)
    z = raw_dir(NAME) / Path(cfg["layers"][NAME]["sdr_url"]).name
    fetched = dt.datetime.fromtimestamp(z.stat().st_mtime).date().isoformat() if z.exists() else dt.date.today().isoformat()
    df = compute(plants, src, cfg, fetched)
    log.info("wells: %d plants; driller logs found for %d (median %d logs); thick caliche/gypsum share median %.2f; "
             "depth to water for %d (median %.0f ft)", len(df), int(df["thick_hard_layer_share"].notna().sum()),
             int(df.loc[df["thick_hard_layer_share"].notna(), "n_logs"].median() or 0), df["thick_hard_layer_share"].median(),
             int(df["depth_to_water_ft"].notna().sum()), df["depth_to_water_ft"].median())
    return g.write_layer(NAME, df)
