"""Layer 9: load-pocket proximity (Section D, 5 pts) from a hand-maintained table of large loads.

ERCOT's large-load queue is not a dataset until the PUCT transparency rule lands (plan §3 change, due Dec 2026), so the
input is data/load_pockets.csv, committed like the crosswalk: one row per project with a source URL. What counts (owner
decision 6 Oct 2026): data-center campuses of any size with a named site, and other large flexible loads (crypto,
hydrogen, industrial) of at least layers.load_pocket.min_load_mw_non_dc. Cancelled projects stay in the table, ignored.

Output data/layers/load_pocket.parquet, one row per eia_id (distances edge of the array to the project point, miles):
    dist_load_pocket_firm_mi        nearest operating / under-construction project; null = none within search radius
    nearest_firm_project, nearest_firm_kind, nearest_firm_status, nearest_firm_mw
    dist_load_pocket_announced_mi   nearest announced project (scores at announced_factor of the band)
    nearest_announced_project, nearest_announced_kind, nearest_announced_mw
    n_projects_within_search, mw_within_search (sum of published MW), projects_within_search ('name (status, MW, mi)')
    load_pocket_location_confidence (site | city | county, of the nearer scoring project)
    load_pocket_search_mi, load_pocket_source, load_pocket_table_date, load_pocket_confidence (always 'manual'), run_id
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from pipeline.common import DATA_DIR, load_config
from pipeline.phase_4_geo import common as g

log = logging.getLogger(__name__)
NAME = "load_pocket"
COLUMNS = ["project_id", "name", "developer", "kind", "status", "load_mw", "load_mw_basis", "county", "city", "lat", "lon",
           "location_confidence", "source_url", "source_date", "notes", "added_by", "added_on", "verified_by", "verified_on"]
KINDS = {"data_center", "crypto", "hydrogen", "industrial", "other"}
STATUSES = {"operating", "under_construction", "announced", "cancelled"}
LOCATION_CONFIDENCE = {"site", "city", "county"}
TX_BBOX = (-106.7, 25.8, -93.5, 36.6)


def table_path(cfg: dict) -> Path:
    return DATA_DIR.parent / cfg["layers"][NAME]["table"]


def read_table(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False).replace({"": None})
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    for c in ("load_mw", "lat", "lon"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[COLUMNS]


def validate(df: pd.DataFrame) -> list[str]:
    """Problems that make a row unusable or the table inconsistent; an empty list means clean."""
    errs = []
    dup = df["project_id"][df["project_id"].duplicated()].dropna().unique()
    if len(dup):
        errs.append(f"duplicate project_id: {', '.join(dup)}")
    for i, r in df.iterrows():
        tag = r["project_id"] or f"row {i + 2}"
        if not r["project_id"]:
            errs.append(f"{tag}: project_id is blank")
        if r["kind"] not in KINDS:
            errs.append(f"{tag}: kind {r['kind']!r} not in {sorted(KINDS)}")
        if r["status"] not in STATUSES:
            errs.append(f"{tag}: status {r['status']!r} not in {sorted(STATUSES)}")
        if r["location_confidence"] not in LOCATION_CONFIDENCE:
            errs.append(f"{tag}: location_confidence {r['location_confidence']!r} not in {sorted(LOCATION_CONFIDENCE)}")
        if pd.isna(r["lat"]) or pd.isna(r["lon"]):
            errs.append(f"{tag}: lat/lon missing")
        elif not (TX_BBOX[0] <= r["lon"] <= TX_BBOX[2] and TX_BBOX[1] <= r["lat"] <= TX_BBOX[3]):
            errs.append(f"{tag}: lat/lon {r['lat']}, {r['lon']} outside Texas")
        if not r["source_url"]:
            errs.append(f"{tag}: source_url is blank")
    return errs


def qualifying(df: pd.DataFrame, c: dict) -> pd.DataFrame:
    """Rows that count: not cancelled; data centers of any size, other kinds at or above min_load_mw_non_dc."""
    live = df["status"].isin([*c["statuses_firm"], *c["statuses_announced"]])
    big = df["kind"].eq("data_center") | (df["load_mw"] >= c["min_load_mw_non_dc"])
    return df[live & big & df["lat"].notna() & df["lon"].notna()].reset_index(drop=True)


def to_points(df: pd.DataFrame) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326")


def compute(plants: gpd.GeoDataFrame, projects: pd.DataFrame, cfg: dict, table_date: str | None) -> pd.DataFrame:
    c = cfg["layers"][NAME]
    r = c["search_radius_mi"]
    pts = to_points(projects)
    keep = ["name", "kind", "status", "load_mw", "location_confidence"]
    out = pd.DataFrame({"eia_id": plants["eia_id"].to_numpy()})
    for group, statuses in (("firm", c["statuses_firm"]), ("announced", c["statuses_announced"])):
        sub = pts[pts["status"].isin(statuses)]
        near = g.nearest(plants, sub if len(sub) else None, keep, group)
        far = near[f"{group}_mi"] > r
        near.loc[far, [f"{group}_mi", *[f"{group}_{k}" for k in keep]]] = None
        out = out.merge(near.rename(columns={
            f"{group}_mi": f"dist_load_pocket_{group}_mi", f"{group}_name": f"nearest_{group}_project",
            f"{group}_kind": f"nearest_{group}_kind", f"{group}_status": f"nearest_{group}_status",
            f"{group}_load_mw": f"nearest_{group}_mw", f"{group}_location_confidence": f"_{group}_loc"}), on="eia_id")
    out["dist_load_pocket_firm_mi"] = out["dist_load_pocket_firm_mi"].astype(float)
    out["dist_load_pocket_announced_mi"] = out["dist_load_pocket_announced_mi"].astype(float)
    out = out.drop(columns=["nearest_announced_status"])

    # every qualifying project within the radius, nearest first
    idx = g.within(plants, pts, r)
    P = plants.set_index("eia_id").to_crs(g.METRIC_CRS)
    F = pts.to_crs(g.METRIC_CRS)
    lists, ns, mws = [], [], []
    for pid in out["eia_id"]:
        hit = F.iloc[idx[int(pid)]]
        d = hit.distance(P.geometry.loc[pid]) / g.M_PER_MI if len(hit) else pd.Series(dtype=float)
        hit = hit.assign(_d=d.to_numpy()).sort_values("_d")
        ns.append(len(hit))
        mws.append(float(hit["load_mw"].sum()) if len(hit) else 0.0)
        lists.append("; ".join(f"{h['name']} ({h['status']}, {'' if pd.isna(h['load_mw']) else f'{h.load_mw:.0f} MW, '}{h['_d']:.1f} mi)"
                               for _, h in hit.iterrows()) or None)
    out["n_projects_within_search"] = ns
    out["mw_within_search"] = mws
    out["projects_within_search"] = lists

    # location confidence of whichever project drives the score (firm wins a tie: it scores full band)
    pts_firm = out["dist_load_pocket_firm_mi"].map(lambda v: band_pts(v, cfg))
    pts_ann = out["dist_load_pocket_announced_mi"].map(lambda v: band_pts(v, cfg)) * announced_factor(cfg)
    out["load_pocket_location_confidence"] = np.where(pts_firm.fillna(0) >= pts_ann.fillna(0), out["_firm_loc"], out["_announced_loc"])
    out = out.drop(columns=["_firm_loc", "_announced_loc"])
    out["load_pocket_search_mi"] = r
    out["load_pocket_source"] = f"manual: {c['table']} ({len(projects)} qualifying projects)"
    out["load_pocket_table_date"] = table_date
    out["load_pocket_confidence"] = "manual"
    return out


def score_spec(cfg: dict) -> dict:
    return cfg["scoring"]["D_electrical"]["load_pocket_proximity"]


def announced_factor(cfg: dict) -> float:
    return float(score_spec(cfg)["announced_factor"])


def band_pts(v, cfg: dict) -> float:
    """Distance band points (no announced discount); NaN → NaN."""
    if v is None or pd.isna(v):
        return np.nan
    for b in score_spec(cfg)["bands"]:
        if "else" in b:
            return float(b["else"])
        if v < b["lt"]:
            return float(b["pts"])
    return 0.0


def run() -> Path:
    cfg = load_config()
    path = table_path(cfg)
    if not path.exists():
        raise SystemExit(f"{path} is missing: it is the hand-maintained input of this layer (see data/README.md)")
    table = read_table(path)
    errs = validate(table)
    if errs:
        raise SystemExit("load_pockets.csv has problems:\n  " + "\n  ".join(errs))
    projects = qualifying(table, cfg["layers"][NAME])
    dates = pd.concat([table["added_on"], table["source_date"]]).dropna()
    table_date = max(dates) if len(dates) else dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
    log.info("load pockets: %d rows, %d qualifying (%s), %d verified", len(table), len(projects),
             ", ".join(f"{k} {v}" for k, v in projects.groupby("status").size().items()), int(table["verified_by"].notna().sum()))
    plants = g.load_plants()
    df = compute(plants, projects, cfg, str(table_date)[:10])
    log.info("load pocket: firm project within %d mi for %d of %d plants (median %.1f mi); announced only for %d",
             cfg["layers"][NAME]["search_radius_mi"], int(df["dist_load_pocket_firm_mi"].notna().sum()), len(df),
             df["dist_load_pocket_firm_mi"].median(),
             int((df["dist_load_pocket_firm_mi"].isna() & df["dist_load_pocket_announced_mi"].notna()).sum()))
    return g.write_layer(NAME, df)
