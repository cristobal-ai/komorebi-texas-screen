"""Phase 2 — plant master: EIA-860M PV generators (TX) rolled up to plants, joined to USPVDB on eia_id.

Every Texas PV plant in EIA-860M is kept in the output with `filter_status` and `filter_reasons`, so the
hand-check can see why a plant dropped. Downstream phases use `filter_status == 'pass'`.

filter_status:
  pass    — every hard filter evaluated and passed
  review  — no hard filter failed, but footprint could not be evaluated (no USPVDB polygon for the eia_id)
  fail    — at least one hard filter failed (reasons listed)

Poor performance is a price signal, not a defect: nothing here filters on output, age of modules or curtailment.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from pipeline.common import DATA_DIR, load_config, raw_dir

log = logging.getLogger(__name__)

OUT_PATH = DATA_DIR / "plants.parquet"

# EIA-860M column → plant-master name
EIA_COLS = {
    "Plant ID": "eia_id",
    "Plant Name": "plant_name",
    "Entity Name": "operator",
    "Plant State": "state",
    "County": "county",
    "Balancing Authority Code": "ba_code",
    "Nameplate Capacity (MW)": "nameplate_mw",
    "DC Net Capacity (MW)": "dc_mw",
    "Operating Month": "op_month",
    "Operating Year": "op_year",
    "Energy Source Code": "energy_source",
    "Prime Mover Code": "prime_mover",
    "Latitude": "lat",
    "Longitude": "lon",
}
STORAGE_ENERGY_SOURCE = "MWH"


def _first_mode(s: pd.Series):
    s = s.dropna()
    return s.mode().iat[0] if len(s) else None


def eia_plants(gen: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Roll EIA-860M PV generators up to one row per plant. AC capacity = sum of PV nameplate (EIA PV nameplate is AC)."""
    src = cfg["sources"]["eia860m"]
    g = gen.rename(columns={k: v for k, v in EIA_COLS.items() if k in gen.columns}).copy()
    if "dc_mw" not in g.columns:
        g["dc_mw"] = np.nan
    for c in ("nameplate_mw", "dc_mw", "op_month", "op_year", "lat", "lon"):
        g[c] = pd.to_numeric(g[c], errors="coerce")
    g = g[g["state"] == src["state"]]
    storage_ids = set(g.loc[g["energy_source"] == STORAGE_ENERGY_SOURCE, "eia_id"])
    if "source_month" not in g.columns:
        g["source_month"] = None
    pv = g[(g["energy_source"] == src["pv_energy_source"]) & (g["prime_mover"] == src["pv_prime_mover"])].copy()
    pv["cod"] = pd.to_datetime(
        dict(year=pv["op_year"], month=pv["op_month"].fillna(1), day=1), errors="coerce"
    )
    agg = pv.groupby("eia_id").agg(
        plant_name=("plant_name", "first"),
        operator=("operator", _first_mode),
        county=("county", _first_mode),
        ba_code=("ba_code", _first_mode),
        ac_mw=("nameplate_mw", "sum"),
        dc_mw_eia=("dc_mw", lambda s: s.sum(min_count=1)),
        dc_reported_gens=("dc_mw", "count"),
        n_generators=("nameplate_mw", "size"),
        cod_first=("cod", "min"),
        cod_last=("cod", "max"),
        lat=("lat", "first"),
        lon=("lon", "first"),
        source_month=("source_month", "first"),
    ).reset_index()
    # A partial DC sum would understate ILR; only trust it when every PV generator reported DC.
    agg.loc[agg["dc_reported_gens"] < agg["n_generators"], "dc_mw_eia"] = np.nan
    agg["has_colocated_storage"] = agg["eia_id"].isin(storage_ids)
    agg["cod_multi_phase"] = agg["cod_first"] != agg["cod_last"]
    return agg.drop(columns="dc_reported_gens")


def uspvdb_plants(poly: gpd.GeoDataFrame, cfg: dict) -> gpd.GeoDataFrame:
    """One row per eia_id: polygons dissolved, p_area summed. Also returns TX rows lacking an eia_id via .attrs."""
    m2_per_acre = cfg["units"]["m2_per_acre"]
    tx = poly[poly["p_state"] == cfg["sources"]["eia860m"]["state"]].copy()
    tx["eia_id"] = pd.to_numeric(tx["eia_id"], errors="coerce")
    no_id = tx[tx["eia_id"].isna()]
    tx = tx[tx["eia_id"].notna()].copy()
    tx["eia_id"] = tx["eia_id"].astype(int)
    tx["_area_calc_m2"] = tx.geometry.to_crs(cfg["sources"]["uspvdb"]["area_crs"]).area
    for c in ("p_area", "p_cap_ac", "p_cap_dc", "p_year"):
        if c in tx.columns:
            tx[c] = pd.to_numeric(tx[c], errors="coerce")
        else:
            tx[c] = np.nan
    for c in ("p_axis", "p_tech_pri", "p_county", "case_id"):
        if c not in tx.columns:
            tx[c] = None
    agg = tx.dissolve(
        by="eia_id",
        aggfunc={
            "p_area": "sum",
            "_area_calc_m2": "sum",
            "p_cap_ac": "sum",
            "p_cap_dc": lambda s: s.sum(min_count=1),
            "p_year": "min",
            "p_axis": _first_mode,
            "p_tech_pri": _first_mode,
            "p_county": _first_mode,
            "case_id": "size",
        },
    ).reset_index()
    agg = agg.rename(
        columns={
            "p_cap_ac": "ac_mw_uspvdb",
            "p_cap_dc": "dc_mw_uspvdb",
            "p_year": "year_uspvdb",
            "p_axis": "tracking_uspvdb",
            "p_tech_pri": "tech_uspvdb",
            "case_id": "n_polygons",
        }
    )
    agg["array_acres"] = agg["p_area"] / m2_per_acre
    agg["array_acres_calc"] = agg["_area_calc_m2"] / m2_per_acre
    agg = agg.drop(columns=["p_area", "_area_calc_m2"])
    agg.attrs["tx_rows_without_eia_id"] = len(no_id)
    return agg


def assign_tier(ac_mw: pd.Series, tiers: dict) -> pd.Series:
    out = pd.Series(pd.NA, index=ac_mw.index, dtype="object")
    for name, t in tiers.items():
        hi = t["max_ac_mw"] if t["max_ac_mw"] is not None else np.inf
        out[(ac_mw >= t["min_ac_mw"]) & (ac_mw < hi)] = name
    return out


def apply_filters(df: pd.DataFrame, cfg: dict, as_of: dt.date) -> pd.DataFrame:
    hf = cfg["hard_filters"]
    a = cfg["assumptions"]
    df = df.copy()

    # ILR: EIA-860M DC → USPVDB DC → config default, with provenance
    df["dc_mw"] = df["dc_mw_eia"].fillna(df["dc_mw_uspvdb"])
    df["dc_mw_source"] = np.select(
        [df["dc_mw_eia"].notna(), df["dc_mw_uspvdb"].notna()], ["eia860m", "uspvdb"], default="ilr_default"
    )
    df["ilr"] = (df["dc_mw"] / df["ac_mw"]).where(df["dc_mw"].notna(), a["ilr_default"])
    df["ilr_confidence"] = np.where(df["dc_mw_source"] == "ilr_default", "assumed", "reported")

    df["acres_per_mw_ac"] = df["array_acres"] / df["ac_mw"]

    cod_from = pd.Timestamp(hf["cod_from"])
    cod_to = pd.Timestamp(hf["cod_to"])
    reasons = pd.Series([[] for _ in range(len(df))], index=df.index)

    def fail(mask, why):
        for i in df.index[mask.fillna(False)]:
            reasons[i].append(why)

    fail(df["ac_mw"] < hf["min_ac_mw"], f"ac_mw<{hf['min_ac_mw']}")
    fail(df["cod_first"].isna(), "cod_missing")
    fail(df["cod_first"] < cod_from, f"cod<{cod_from.date()}")
    fail(df["cod_first"] > cod_to, f"cod>{cod_to.date()}")
    fail(df["array_acres"] < hf["min_acres"], f"acres<{hf['min_acres']}")
    fail(df["acres_per_mw_ac"] < hf["min_acres_per_mw_ac"], f"acres_per_mw<{hf['min_acres_per_mw_ac']}")

    # Filter 3 — flag, do not drop (brief §2.3, plan §3.1)
    df["non_ercot_texas"] = df["ba_code"].fillna("") != cfg["sources"]["eia860m"]["ercot_ba_code"]
    df["ercot_flag"] = ~df["non_ercot_texas"]

    df["filter_reasons"] = reasons.map(lambda r: ";".join(r))
    no_polygon = df["array_acres"].isna()
    df["filter_status"] = np.where(
        df["filter_reasons"] != "", "fail", np.where(no_polygon, "review", "pass")
    )
    df.loc[no_polygon & (df["filter_status"] == "review"), "filter_reasons"] = "no_uspvdb_polygon"

    # Tiers and SB6 load (plan §3.2): planned_load = AC × ILR × IT/MWdc × PUE
    df["tier"] = assign_tier(df["ac_mw"], cfg["tiers"])
    df["firm_it_mw"] = df["ac_mw"] * df["ilr"] * a["it_mw_per_mwdc"]
    df["planned_load_mw"] = df["firm_it_mw"] * a["pue_assumed"]
    df["sb6_review_required"] = df["planned_load_mw"] >= cfg["sb6"]["large_load_threshold_mw"]

    # Caveat flags that depend only on COD (plan §3.5); displayed, never scored
    df["tax_equity_consent_likely"] = df["cod_first"].dt.year >= 2019
    itc_cutoff = pd.Timestamp(as_of) - pd.DateOffset(years=5)
    df["itc_recapture_open"] = df["cod_last"] > itc_cutoff

    df["ac_mw_delta_pct"] = (df["ac_mw_uspvdb"] - df["ac_mw"]) / df["ac_mw"]
    df["acres_calc_delta_pct"] = (df["array_acres_calc"] - df["array_acres"]) / df["array_acres"]
    df["module_type_confidence"] = "proxy"
    return df


def build(gen: pd.DataFrame, poly: gpd.GeoDataFrame, cfg: dict | None = None, as_of: dt.date | None = None):
    cfg = cfg or load_config()
    as_of = as_of or dt.date.today()
    eia = eia_plants(gen, cfg)
    usp = uspvdb_plants(poly, cfg)
    merged = eia.merge(usp, on="eia_id", how="left", indicator=True)
    merged["uspvdb_match"] = merged.pop("_merge") == "both"
    plants = gpd.GeoDataFrame(apply_filters(merged, cfg, as_of), geometry="geometry", crs=usp.crs)
    orphans = usp[~usp["eia_id"].isin(eia["eia_id"])].copy()
    orphans.attrs["tx_rows_without_eia_id"] = usp.attrs.get("tx_rows_without_eia_id", 0)
    return plants, orphans


def run(as_of: dt.date | None = None) -> Path:
    gen = pd.read_parquet(raw_dir("eia860m") / "eia860m_latest.parquet")
    poly = gpd.read_parquet(raw_dir("uspvdb") / "uspvdb.parquet")
    plants, orphans = build(gen, poly, as_of=as_of)
    plants.to_parquet(OUT_PATH)
    orphans.to_parquet(DATA_DIR / "uspvdb_orphans_tx.parquet")
    counts = plants["filter_status"].value_counts().to_dict()
    log.info(
        "plant master: %d TX PV plants %s; %d USPVDB TX polygons with eia_id not in EIA-860M operating; → %s",
        len(plants), counts, len(orphans), OUT_PATH,
    )
    return OUT_PATH
