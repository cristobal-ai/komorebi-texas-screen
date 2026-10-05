"""ERCOT resource lists for the Phase 3a crosswalk.

    cdr()            CDR workbook (manual download)  → data/raw/ercot_cdr/cdr_units.parquet
    sced_pv(date)    one day of 60-day SCED disclosure, PVGR resources → data/raw/ercot/sced_pv_resources.parquet
    node_to_unit()   public Resource Node ↔ Unit mapping → data/raw/ercot/resource_node_to_unit.parquet

The SCED call needs ERCOT_API_USERNAME / _PASSWORD / _SUBSCRIPTION_KEY (pipeline/.env); the other two do not.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path

import pandas as pd

from pipeline.common import find_col, load_config, manual_file, norm, raw_dir

log = logging.getLogger(__name__)


def _strings(df: pd.DataFrame) -> pd.DataFrame:
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].map(lambda v: None if pd.isna(v) else str(v).strip())
    return df


def read_cdr(path: Path, cfg: dict | None = None) -> pd.DataFrame:
    """Every sheet with a header row containing the unit-code column, concatenated; solar rows only when a fuel
    column exists. Columns are kept as published plus `sheet`."""
    c = (cfg or load_config())["sources"]["ercot"]
    key = norm(c["cdr_key_col"])
    xl = pd.ExcelFile(path)
    frames = []
    for sheet in xl.sheet_names:
        probe = xl.parse(sheet, header=None, nrows=40)
        hits = [i for i, row in probe.iterrows() if any(norm(v) == key for v in row.tolist())]
        if not hits:
            continue
        df = xl.parse(sheet, header=hits[0])
        df.columns = [" ".join(str(x).split()) for x in df.columns]
        code = find_col(df, c["cdr_key_col"], required=True)
        df = df[df[code].notna() & (df[code].astype(str).str.strip() != "")].copy()
        fuel = find_col(df, "Fuel", what="CDR fuel")
        if fuel:
            pat = "|".join(c["cdr_solar_fuel_codes"])
            df = df[df[fuel].astype(str).str.upper().str.contains(pat, na=False)]
        if df.empty:
            continue
        df["sheet"] = sheet
        frames.append(_strings(df))
        log.info("CDR sheet %r: %d solar unit rows", sheet, len(df))
    if not frames:
        raise ValueError(f"no sheet in {path} has a {c['cdr_key_col']!r} header with solar rows (sheets: {xl.sheet_names})")
    return pd.concat(frames, ignore_index=True)


def cdr() -> Path:
    path = manual_file("ercot_cdr", (".xlsx", ".xlsm", ".xls"))
    if not path:
        raise RuntimeError(
            "No CDR workbook found. Download the latest 'Capacity, Demand and Reserves (CDR) Report' XLSX from "
            "https://www.ercot.com/gridinfo/resource into data/raw/ercot_cdr/manual/ and re-run."
        )
    df = read_cdr(path)
    df["source_file"] = path.name
    out = raw_dir("ercot_cdr") / "cdr_units.parquet"
    df.to_parquet(out, index=False)
    log.info("CDR %s: %d solar unit rows → %s", path.name, len(df), out)
    return out


def summarize_sced_pv(gen: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """One row per PV resource: QSE, max HSL (≈ AC capability on a sunny interval), intervals seen."""
    c = (cfg or load_config())["sources"]["ercot"]
    name = find_col(gen, "Resource Name", required=True)
    rtype = find_col(gen, "Resource Type", required=True)
    hsl = find_col(gen, "HSL", required=True)
    qse = find_col(gen, "QSE", what="QSE")
    pv = gen[gen[rtype].astype(str).str.strip().str.upper() == c["sced_pv_resource_type"]].copy()
    pv[hsl] = pd.to_numeric(pv[hsl], errors="coerce")
    agg = {"max_hsl_mw": (hsl, "max"), "intervals": (hsl, "size")}
    if qse:
        agg["qse"] = (qse, "first")
    return pv.groupby(name).agg(**agg).reset_index().rename(columns={name: "resource_name"})


def sced_pv(day: dt.date | None = None) -> Path:
    from gridstatus.ercot_api.ercot_api import ErcotAPI

    c = load_config()["sources"]["ercot"]
    day = day or (dt.date.today() - dt.timedelta(days=c["sced_days_back"]))
    api = ErcotAPI(
        username=os.environ.get("ERCOT_API_USERNAME"),
        password=os.environ.get("ERCOT_API_PASSWORD"),
        public_subscription_key=os.environ.get("ERCOT_API_SUBSCRIPTION_KEY"),
    )
    log.info("ERCOT 60-day SCED disclosure for %s (one day, ~tens of MB)", day)
    # gridstatus shifts the date by +60 days internally to find the published file
    data = api.get_60_day_sced_disclosure(date=pd.Timestamp(day), process=False)
    df = summarize_sced_pv(data["sced_gen_resource"])
    df["sced_day"] = day.isoformat()
    out = raw_dir("ercot") / "sced_pv_resources.parquet"
    df.to_parquet(out, index=False)
    log.info("SCED %s: %d PVGR resources → %s", day, len(df), out)
    return out


def node_to_unit() -> Path:
    from gridstatus import Ercot

    df = _strings(Ercot().get_resource_node_to_unit(date="latest"))
    out = raw_dir("ercot") / "resource_node_to_unit.parquet"
    df.to_parquet(out, index=False)
    log.info("Resource node ↔ unit mapping: %d rows → %s", len(df), out)
    return out
