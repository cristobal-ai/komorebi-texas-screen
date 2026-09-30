"""Hand-check report for one county (Pecos first, then Falls/Milam): count, MW, COD, acres/MW.

Writes data/validation/<county>_handcheck.md and .csv. Nothing is written to Supabase from here — the
report is what you compare against the USPVDB viewer, ERCOT's resource list and satellite imagery before
Phase 2 output is loaded.
"""
from __future__ import annotations

import datetime as dt
import numbers
from pathlib import Path

import geopandas as gpd
import pandas as pd

from pipeline.common import DATA_DIR, load_config
from pipeline.phase_2_normalize.plant_master import sb6_line_ac_mw

VALIDATION_DIR = DATA_DIR / "validation"

COLUMNS = [
    "eia_id", "plant_name", "operator", "ba_code", "n_generators", "ac_mw", "ac_mw_uspvdb", "ac_mw_delta_pct",
    "dc_mw", "dc_mw_source", "ilr", "cod_first", "cod_last", "year_uspvdb", "array_acres", "array_acres_calc",
    "acres_per_mw_ac", "n_polygons", "tracking_uspvdb", "has_colocated_storage", "tier", "planned_load_mw",
    "sb6_review_required", "filter_status", "filter_reasons",
    "grid_voltage_kv", "grid_voltage_max_kv", "distribution_class_poi", "tracking_type", "tracking_type_share",
    "module_tech", "bifacial_share", "net_mwh", "net_ac_cf", "cf_year", "cf_series_resolution", "cf_note",
]


def _fmt(v, nd=1):
    if v is None or v is pd.NA or v is pd.NaT or (isinstance(v, numbers.Real) and pd.isna(v)):
        return "—"
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m")
    if isinstance(v, numbers.Real) and not isinstance(v, bool):
        return f"{v:,.{nd}f}"
    return str(v)


def _pct(v, nd=0):
    return "—" if v is None or pd.isna(v) else f"{v * 100:.{nd}f}%"


def report(plants: gpd.GeoDataFrame, orphans: gpd.GeoDataFrame, county: str, out_dir: Path = VALIDATION_DIR,
           cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    out_dir.mkdir(parents=True, exist_ok=True)
    key = county.strip().lower()
    p = plants[plants["county"].fillna("").str.strip().str.lower() == key].sort_values("ac_mw", ascending=False)
    o = orphans[orphans["p_county"].fillna("").str.strip().str.lower().str.startswith(key)]

    p[[c for c in COLUMNS if c in p.columns]].to_csv(out_dir / f"{key}_handcheck.csv", index=False, encoding="utf-8-sig")

    cf_line = ""
    if "net_ac_cf" in p.columns:
        cf_ok = p[(p["filter_status"] != "fail") & p["net_ac_cf"].notna()]
        yr = p["cf_year"].dropna()
        cf_line = (f"- Net AC capacity factor (EIA-923 {int(yr.iat[0]) if len(yr) else '—'}): {len(cf_ok)} pass/review "
                   f"plants with a value; median {_pct(cf_ok['net_ac_cf'].median(), 1)}, "
                   f"range {_pct(cf_ok['net_ac_cf'].min(), 1)}–{_pct(cf_ok['net_ac_cf'].max(), 1)}")
    by_status = p.groupby("filter_status").agg(plants=("eia_id", "size"), ac_mw=("ac_mw", "sum"))
    passed = p[p["filter_status"] == "pass"]
    lines = [
        f"# {county} County hand-check — {dt.date.today():%Y-%m-%d}",
        "",
        f"EIA-860M data month: {_fmt(p['source_month'].dropna().iat[0] if p['source_month'].notna().any() else None)}",
        "",
        f"T1a/T1b line (derived): {sb6_line_ac_mw(cfg):,.2f} MW AC · footprint basis: "
        f"{cfg['hard_filters']['footprint_basis']} (array-area acreage → review, not fail)"
        if cfg["hard_filters"]["footprint_basis"] == "array_flag"
        else f"T1a/T1b line (derived): {sb6_line_ac_mw(cfg):,.2f} MW AC · footprint basis: parcel",
        "",
        "## Totals",
        "",
        f"- Texas PV plants in EIA-860M for {county}: **{len(p)}**, {p['ac_mw'].sum():,.1f} MW AC",
        f"- Pass all hard filters: **{len(passed)}**, {passed['ac_mw'].sum():,.1f} MW AC",
        f"- Matched to a USPVDB polygon: {int(p['uspvdb_match'].sum())} of {len(p)}",
        f"- USPVDB polygons in {county} whose eia_id is not in EIA-860M operating: {len(o)}",
        *([cf_line] if cf_line else []),
        "",
        "| status | plants | MW AC |",
        "|---|---:|---:|",
        *[f"| {r.Index} | {int(r.plants)} | {r.ac_mw:,.1f} |" for r in by_status.itertuples()],
        "",
        "## Plants (largest first)",
        "",
        "| EIA ID | Plant | MW AC (EIA) | MW AC (USPVDB) | Δ | MW DC | ILR | COD first | COD last | Acres | Acres/MW | Tier | Load MW | SB6 | POI kV | Tracking | Module | Bifacial | CF | CF res | Status | Reasons |",
        "|---:|---|---:|---:|---:|---:|---:|---|---|---:|---:|---|---:|---|---:|---|---|---:|---:|---|---|---|",
    ]
    for _, r in p.iterrows():
        lines.append(
            "| " + " | ".join([
                str(r.eia_id), _fmt(r.plant_name), _fmt(r.ac_mw), _fmt(r.ac_mw_uspvdb),
                _fmt(r.ac_mw_delta_pct * 100, 0) + "%" if pd.notna(r.ac_mw_delta_pct) else "—",
                _fmt(r.dc_mw) + ("" if r.dc_mw_source == "eia860m" else f" ({r.dc_mw_source})"),
                _fmt(r.ilr, 2), _fmt(r.cod_first), _fmt(r.cod_last), _fmt(r.array_acres, 0),
                _fmt(r.acres_per_mw_ac), _fmt(r.tier), _fmt(r.planned_load_mw), "yes" if r.sb6_review_required else "no",
                _fmt(r.get("grid_voltage_kv"), 1), _fmt(r.get("tracking_type")), _fmt(r.get("module_tech")),
                _pct(r.get("bifacial_share")),
                _pct(r.get("net_ac_cf"), 1) + (f" ({r.get('cf_note')})" if isinstance(r.get("cf_note"), str) else ""),
                _fmt(r.get("cf_series_resolution")),
                r.filter_status, _fmt(r.filter_reasons or None),
            ]) + " |"
        )
    if len(o):
        lines += ["", "## USPVDB polygons with no operating EIA-860M plant", "",
                  "| eia_id | Array acres | MW AC (USPVDB) | Year |", "|---:|---:|---:|---:|"]
        for _, r in o.iterrows():
            lines.append(f"| {r.eia_id} | {_fmt(r.array_acres, 0)} | {_fmt(r.ac_mw_uspvdb)} | {_fmt(r.year_uspvdb, 0)} |")
    lines += [
        "", "## What to check by hand", "",
        "1. Count — every utility-scale array visible on imagery in the county appears above (or in the orphan list).",
        "2. MW — EIA vs USPVDB AC agree within ~5%; larger Δ usually means a phase split across EIA IDs.",
        "3. COD — `cod_first` matches the plant's announced COD; multi-phase plants show two dates.",
        "4. Acres/MW — Texas single-axis trackers typically run 6–9 acres/MW AC; outliers mean a polygon error.",
        "5. Every `review` row: find the missing polygon (USPVDB viewer) or confirm the plant has none.",
        "6. POI kV — matches the interconnecting line (ERCOT GIS report / OSM); ≤34.5 kV is distribution-class.",
        "7. CF — West Texas single-axis PV typically nets ~22–32% AC; low values are a price signal, not an error.",
    ]
    md = out_dir / f"{key}_handcheck.md"
    # BOM so Windows PowerShell 5.1 Get-Content and Notepad detect UTF-8 (Δ, —, → otherwise show as mojibake)
    md.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return md
