"""Plant attributes from EIA-860 (annual) and capacity factor from EIA-923, keyed on eia_id.

EIA-860 plant file  → grid_voltage_kv (POI), grid_voltage_max_kv, distribution_class_poi
EIA-860 3_3 solar   → tracking_type (+ share of MW), module_tech (+ share), bifacial_share   [MW-weighted]
EIA-923 page 1      → net_mwh, net_ac_cf, cf_year, cf_series_resolution ('monthly' | 'annual'), cf_note

A null CF always carries a cf_note saying why; nothing here drops a plant. Low CF is a price signal, not a defect,
so only physically implausible values are noted.
"""
from __future__ import annotations

import calendar
import logging

import numpy as np
import pandas as pd

from pipeline.common import find_col

log = logging.getLogger(__name__)

MONTHS = [calendar.month_name[m] for m in range(1, 13)]
# EIA-860 3_3 Y/N flag columns → category. A generator with more than one Y is 'mixed'.
TRACKING_FLAGS = {
    "Single-Axis Tracking?": "single_axis",
    "Dual-Axis Tracking?": "dual_axis",
    "East West Fixed Tilt?": "fixed_east_west",
    "Fixed Tilt?": "fixed",
}
MODULE_FLAGS = {
    "Crystalline Silicon?": "c-Si",
    "Thin-Film (CdTe)?": "thin_film_CdTe",
    "Thin-Film (A-Si)?": "thin_film_a-Si",
    "Thin-Film (CIGS)?": "thin_film_CIGS",
    "Thin-Film (Other)?": "thin_film_other",
    "Other Materials?": "other",
}


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _yes(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.strip().str.upper().isin(["Y", "YES", "TRUE", "1"])


def _category(solar: pd.DataFrame, flags: dict[str, str], what: str) -> pd.Series:
    cols = {label: find_col(solar, name, what=f"{what}: {name}") for name, label in flags.items()}
    cols = {label: c for label, c in cols.items() if c}
    if not cols:
        return pd.Series(None, index=solar.index, dtype="object")
    ys = pd.DataFrame({label: _yes(solar[c]) for label, c in cols.items()})
    n = ys.sum(axis=1)
    out = ys.idxmax(axis=1).where(n == 1)
    return out.mask(n > 1, "mixed")


def _mw_dominant(df: pd.DataFrame, cat: str) -> pd.DataFrame:
    """Per plant: the category holding the most MW, and its share of the plant's categorised MW."""
    d = df.dropna(subset=[cat])
    if d.empty:
        return pd.DataFrame(columns=["eia_id", cat, f"{cat}_share"])
    mw = d.groupby(["eia_id", cat])["mw"].sum().reset_index()
    tot = mw.groupby("eia_id")["mw"].transform("sum")
    mw[f"{cat}_share"] = mw["mw"] / tot
    top = mw.sort_values(["eia_id", "mw"], ascending=[True, False]).drop_duplicates("eia_id")
    return top[["eia_id", cat, f"{cat}_share"]]


def eia860_attrs(plant: pd.DataFrame, solar: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    dist_kv = cfg["scoring"]["D_electrical"]["poi_voltage_kv"]["distribution_class_max_kv"]

    pid = find_col(plant, "Plant Code", required=True)
    gv = [find_col(plant, n, what=n) for n in ("Grid Voltage (kV)", "Grid Voltage 2 (kV)", "Grid Voltage 3 (kV)")]
    p = pd.DataFrame({"eia_id": plant[pid].astype(int)})
    volts = pd.DataFrame({i: _num(plant[c]) for i, c in enumerate(gv) if c})
    p["grid_voltage_kv"] = volts[0] if 0 in volts else np.nan
    p["grid_voltage_max_kv"] = volts.max(axis=1) if len(volts.columns) else np.nan
    p["eia860_year"] = plant.get("data_year")
    p = p.drop_duplicates("eia_id")
    p["grid_voltage_source"] = np.where(p["grid_voltage_kv"].notna(), "eia860", None)
    p["distribution_class_poi"] = p["grid_voltage_kv"] <= dist_kv

    sid = find_col(solar, "Plant Code", required=True)
    mwc = find_col(solar, "Nameplate Capacity (MW)", what="solar nameplate")
    s = pd.DataFrame({"eia_id": solar[sid].astype(int), "mw": _num(solar[mwc]) if mwc else 1.0})
    s["tracking_type"] = _category(solar, TRACKING_FLAGS, "tracking")
    s["module_tech"] = _category(solar, MODULE_FLAGS, "module")
    bif = find_col(solar, "Bifacial?", what="bifacial")
    if bif:
        answered = solar[bif].fillna("").astype(str).str.strip().str.upper().isin(["Y", "N"])
        s["_bif_mw"] = s["mw"].where(_yes(solar[bif]), 0.0)
        s["_ans_mw"] = s["mw"].where(answered, 0.0)
        b = s.groupby("eia_id")[["_bif_mw", "_ans_mw"]].sum()
        bshare = (b["_bif_mw"] / b["_ans_mw"].replace(0, np.nan)).rename("bifacial_share").reset_index()
    else:
        bshare = pd.DataFrame(columns=["eia_id", "bifacial_share"])

    out = p
    for part in (_mw_dominant(s, "tracking_type"), _mw_dominant(s, "module_tech"), bshare):
        out = out.merge(part, on="eia_id", how="left")
    return out


def eia923_cf(gen: pd.DataFrame, plants: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Annual net AC capacity factor for the data year, only for plants operating the full calendar year."""
    c923 = cfg["sources"]["eia923"]
    lo, hi = cfg["benchmarks"]["cf_plausible_range"]
    pid = find_col(gen, "Plant Id", required=True)
    pm = find_col(gen, "Reported Prime Mover", required=True)
    freq = find_col(gen, "Respondent Frequency", what="respondent frequency")
    total = find_col(gen, "Net Generation (Megawatthours)", "Net Generation", required=True)
    months = [find_col(gen, f"Netgen {m}", what=f"Netgen {m}") for m in MONTHS]

    g = gen[gen[pm].astype(str).str.strip().str.upper() == c923["pv_prime_mover"]].copy()
    g["eia_id"] = g[pid].astype(int)
    g["_mwh"] = _num(g[total])
    mcols = [c for c in months if c]
    g["_months"] = g[mcols].apply(_num).notna().sum(axis=1) if mcols else 0
    g["_freq"] = g[freq].astype(str).str.strip().str.upper() if freq else None
    agg = g.groupby("eia_id").agg(
        net_mwh=("_mwh", lambda s: s.sum(min_count=1)),   # all-NaN stays NaN, not 0
        months_reported=("_months", "max"), freq=("_freq", "first"),
        data_year=("data_year", "first"),
    ).reset_index()

    df = plants[["eia_id", "ac_mw", "cod_last"]].merge(agg, on="eia_id", how="left")
    year = int(gen["data_year"].iloc[0]) if "data_year" in gen and len(gen) else None
    hours = (8784 if calendar.isleap(year) else 8760) if year else np.nan
    df["cf_year"] = year
    df["cf_series_resolution"] = np.select(
        [df["freq"] == "M", df["freq"] == "A"], ["monthly", "annual"], default=None
    )
    cf = df["net_mwh"] / (df["ac_mw"] * hours)

    full_year = df["cod_last"] < pd.Timestamp(year=year or 1900, month=1, day=1)
    no_record = df["net_mwh"].isna()
    short = (df["freq"] == "M") & (df["months_reported"] < c923["min_months_for_full_year"])
    df["cf_note"] = np.select(
        [no_record, ~full_year, short, (cf < lo) | (cf > hi)],
        ["no_eia923_record", "partial_year_cod", "incomplete_months", "outside_plausible_range"],
        default=None,
    )
    df["net_ac_cf"] = cf.where(~(no_record | ~full_year | short))
    return df[["eia_id", "net_mwh", "net_ac_cf", "cf_year", "cf_series_resolution", "months_reported", "cf_note"]]
