"""Phase 3b driver: cached SCED + SPP → plant_metrics_monthly.parquet, plant_metrics.parquet, market_report.md.

Only crosswalk rows that a person verified (verified_by set) feed the metrics; everything else is left out rather
than guessed. Plants with no ERCOT resource stay in the output with metrics_status = 'no_ercot_resource'.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.common import DATA_DIR, load_config
from pipeline.phase_1_ingest import ercot_history
from pipeline.phase_3_market import metrics
from pipeline.phase_3_market.crosswalk import CROSSWALK_CSV

log = logging.getLogger(__name__)
MONTHLY_PATH = DATA_DIR / "plant_metrics_monthly.parquet"
SUMMARY_PATH = DATA_DIR / "plant_metrics.parquet"
REPORT_MD = DATA_DIR / "validation" / "market_report.md"


def verified_crosswalk(path: Path = CROSSWALK_CSV) -> pd.DataFrame:
    xw = pd.read_csv(path, dtype={"verified_by": str}, encoding="utf-8-sig")
    return xw[xw["verified_by"].fillna("").str.strip() != ""].copy()


def compute(sced15: pd.DataFrame, spp15: pd.DataFrame, xw: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    ac = xw.groupby(xw["eia_plant_id"].astype(int))["ac_mw_eia"].first()
    units = metrics.unit_monthly(sced15, spp15, xw, cfg) if len(sced15) else pd.DataFrame()
    monthly = metrics.plant_monthly(units, ac, cfg) if len(units) else pd.DataFrame()
    summary = metrics.plant_summary(monthly, xw, ac, cfg)
    return monthly, summary


def quartiles(s: pd.Series) -> dict[str, float]:
    s = s.dropna()
    return {} if s.empty else {f"q{q}": float(s.quantile(q / 100)) for q in (25, 50, 75)}


def report(summary: pd.DataFrame, plants: pd.DataFrame | None, cfg: dict, window: tuple[dt.date, dt.date],
           path: Path = REPORT_MD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = summary.copy()
    if plants is not None and "eia_id" in plants:
        s = s.merge(plants[["eia_id", "plant_name", "county", "tier", "ac_mw", "net_ac_cf"]], on="eia_id", how="left")
    ok = s[s["metrics_status"] == "ok"]
    pct = lambda v, nd=1: "—" if pd.isna(v) else f"{v * 100:.{nd}f}%"
    num = lambda v, nd=2: "—" if pd.isna(v) else f"{v:,.{nd}f}"
    fixed = cfg["scoring"]["A_acquisition_discount"]
    lines = [
        f"# Phase 3b market metrics — {dt.date.today():%Y-%m-%d}", "",
        f"Window requested: {window[0]} → {window[1]} (ERCOT API history floor {cfg['market_window']['api_history_floor']}). "
        f"Months enter a plant's summary only with ≥ {cfg['market_metrics']['min_month_coverage']:.0%} of days covered.", "",
        "## Status", "",
        *[f"- {k}: {v}" for k, v in s["metrics_status"].value_counts().items()], "",
        "## Fleet distribution (plants with status ok)", "",
        "| metric | n | min | q25 | median | q75 | max |", "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for col, fmt in (("capture_rate", num), ("capture_rate_potential", num), ("shape_capture", num), ("basis_ratio", num), ("curtailment_pct", pct),
                     ("sced_net_cf", pct), ("sced_potential_cf", pct)):
        v = ok[col].dropna()
        stats = [v.min(), v.quantile(.25), v.median(), v.quantile(.75), v.max()] if len(v) else [np.nan] * 5
        lines.append(f"| {col} | {len(v)} | " + " | ".join(fmt(x) for x in stats) + " |")
    qc, qu = quartiles(ok["capture_rate"]), quartiles(ok["curtailment_pct"])
    lines += [
        "", "## Breakpoints", "",
        f"- Fixed capture-rate bands (config): {fixed['capture_rate']['fixed']}",
        f"- Fixed curtailment bands (config): {fixed['curtailment_pct']['fixed']}",
        f"- Fleet quartiles — capture_rate: {', '.join(f'{k} {num(v, 3)}' for k, v in qc.items()) or '—'}",
        f"- Fleet quartiles — curtailment_pct: {', '.join(f'{k} {pct(v)}' for k, v in qu.items()) or '—'}",
        "- Switch `scoring.breakpoint_mode` to `quantile` once these look sane (plan §3 change 5).", "",
        "## Plants", "",
        "| EIA ID | Plant | County | Tier | MW | Months | Capture | Shape | Basis | Curtail | SCED CF | Potential CF | Peak HSL/MW | Peak last 3 mo | Pot. capture | EIA-923 CF | Status |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in s.sort_values("capture_rate", na_position="last").itertuples():
        lines.append("| " + " | ".join([
            str(r.eia_id), str(getattr(r, "plant_name", "—")), str(getattr(r, "county", "—")), str(getattr(r, "tier", "—")),
            num(getattr(r, "ac_mw", np.nan), 1), str(r.metrics_window_months), num(r.capture_rate), num(r.shape_capture),
            num(r.basis_ratio), pct(r.curtailment_pct), pct(r.sced_net_cf), pct(r.sced_potential_cf), num(r.peak_hsl_ratio), num(r.peak_hsl_ratio_recent), num(r.capture_rate_potential),
            pct(getattr(r, "net_ac_cf", np.nan)), r.metrics_status + (" (shared unit)" if r.ercot_resource_shared else ""),
        ]) + " |")
    lines += ["", "## What to check by hand", "",
              "1. Monthly capture rate sits below 1.0 for West Texas solar and moves with the summer/winter price shape.",
              "2. `SCED CF` is close to `EIA-923 CF` (differences: terminal vs POI metering, window vs calendar year).",
              "3. Curtailment is highest in spring and in West Texas; a plant at 0.0% in every month is suspect (Base Point = HSL?).",
              "4. Compare one plant-month against ERCOT's own settlement data or a published Modo/ERCOT curtailment figure."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def run(since: dt.date | None = None, until: dt.date | None = None, fetch: bool = True, retry_missing: bool = False) -> Path:
    cfg = load_config()
    xw = verified_crosswalk()
    start, end = ercot_history.window(cfg, since, until)
    if fetch:
        ercot_history.run(since, until, xw, retry_missing=retry_missing)
    log.info("reading cached SCED/SPP days %s → %s ...", start, end)
    wanted = {v.strip() for v in xw["ercot_resource_name"].dropna().astype(str)}
    sced15, spp15 = ercot_history.load_cached(start, end, wanted)
    log.info("loaded %d SCED rows for %d resources, %d price rows; computing metrics ...", len(sced15),
             sced15["resource_name"].nunique() if len(sced15) else 0, len(spp15))
    if sced15.empty:
        raise RuntimeError(f"no cached SCED days between {start} and {end}; run with fetch enabled on a machine that reaches ERCOT")
    monthly, summary = compute(sced15, spp15, xw, cfg)
    monthly.to_parquet(MONTHLY_PATH, index=False)
    summary.to_parquet(SUMMARY_PATH, index=False)
    pp = DATA_DIR / "plants.parquet"
    plants = pd.read_parquet(pp) if pp.exists() else None
    md = report(summary, plants, cfg, (start, end))
    log.info("3b: %s months-rows, %d plants → %s, %s; report %s", len(monthly), len(summary), MONTHLY_PATH, SUMMARY_PATH, md)
    return md
