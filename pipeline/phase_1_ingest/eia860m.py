"""EIA-860M monthly generator inventory — 'Operating' sheet, all US generators.

Writes data/raw/eia860m/eia860m_<yyyy>_<mm>.parquet plus eia860m_latest.parquet (a copy of the newest).
Columns are kept as published (e.g. 'Plant ID', 'Nameplate Capacity (MW)', 'DC Net Capacity (MW)',
'Operating Year', 'Balancing Authority Code'); renaming happens in phase 2.
"""
from __future__ import annotations

import calendar
import datetime as dt
import logging
import shutil
from pathlib import Path

import pandas as pd

from pipeline.common import download, load_config, manual_file, raw_dir

log = logging.getLogger(__name__)

SOURCE = "eia860m"
HEADER_KEY = "Plant ID"


def _candidate_months(today: dt.date, lookback: int):
    y, m = today.year, today.month
    for _ in range(lookback + 1):
        yield y, m
        m -= 1
        if m == 0:
            y, m = y - 1, 12


def fetch(today: dt.date | None = None) -> tuple[Path, str]:
    """Return (xlsx path, 'YYYY-MM' data month). Manual override wins; otherwise newest published file."""
    manual = manual_file(SOURCE, (".xlsx",))
    if manual:
        log.info("using manual EIA-860M file %s", manual)
        return manual, _month_from_name(manual.name)
    cfg = load_config()["sources"]["eia860m"]
    today = today or dt.date.today()
    errors = []
    for y, m in _candidate_months(today, cfg["lookback_months"]):
        month = calendar.month_name[m].lower()
        for tmpl in cfg["urls"]:
            url = tmpl.format(month=month, year=y)
            dest = raw_dir(SOURCE) / f"{month}_generator{y}.xlsx"
            try:
                return download(url, dest), f"{y}-{m:02d}"
            except Exception as e:
                errors.append(f"{url}: {e}")
    raise RuntimeError(
        "EIA-860M download failed; put the xlsx in data/raw/eia860m/manual/.\n  " + "\n  ".join(errors[-4:])
    )


def _month_from_name(name: str) -> str:
    stem = name.lower()
    for i, mname in enumerate(calendar.month_name):
        if i and stem.startswith(mname.lower()):
            digits = "".join(ch for ch in stem if ch.isdigit())[:4]
            if len(digits) == 4:
                return f"{digits}-{i:02d}"
    return "unknown"


def read(path: Path, sheet: str | None = None) -> pd.DataFrame:
    """Read the Operating sheet; the header row is found by searching for 'Plant ID' (EIA prepends notes)."""
    sheet = sheet or load_config()["sources"]["eia860m"]["sheet"]
    probe = pd.read_excel(path, sheet_name=sheet, header=None, nrows=15)
    hits = [i for i, row in probe.iterrows() if row.astype(str).str.strip().eq(HEADER_KEY).any()]
    if not hits:
        raise ValueError(f"'{HEADER_KEY}' header not found in {path}:{sheet}")
    df = pd.read_excel(path, sheet_name=sheet, header=hits[0])
    df.columns = [str(c).strip() for c in df.columns]
    df = df[pd.to_numeric(df[HEADER_KEY], errors="coerce").notna()]  # drops EIA footnote rows
    df[HEADER_KEY] = df[HEADER_KEY].astype(int)
    # Mixed-type object columns (e.g. Generator ID '1' vs 'GEN1') break pyarrow; store them as strings.
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].map(lambda v: None if pd.isna(v) else str(v).strip())
    return df


def run(today: dt.date | None = None) -> Path:
    path, month = fetch(today)
    df = read(path)
    df["source_month"] = month
    out = raw_dir(SOURCE) / f"eia860m_{month.replace('-', '_')}.parquet"
    df.to_parquet(out, index=False)
    shutil.copyfile(out, raw_dir(SOURCE) / "eia860m_latest.parquet")
    log.info("EIA-860M %s: %d operating generators → %s", month, len(df), out)
    return out
