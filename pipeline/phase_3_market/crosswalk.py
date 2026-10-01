"""Phase 3a — EIA plant ↔ ERCOT resource crosswalk.

Each pass/review plant is matched to CDR solar units (unit code = ERCOT resource name) on county, normalized-name
similarity, MW and in-service year; SCED PVGR coverage and the resource-node settlement point are attached when
those raw files exist. Output rows go to data/crosswalk_eia_ercot.csv (committed). Rows with `verified_by` set are
never touched, and a plant with any verified row gets no new automatic candidates.

match_confidence: high | medium | low | none. Thresholds: config.yaml `crosswalk`.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

from pipeline.common import DATA_DIR, find_col, load_config, raw_dir

log = logging.getLogger(__name__)

CROSSWALK_CSV = DATA_DIR / "crosswalk_eia_ercot.csv"
REVIEW_MD = DATA_DIR / "validation" / "crosswalk_review.md"
# Committed schema (data/README.md) followed by the evidence columns used to judge a match.
CSV_COLUMNS = [
    "eia_plant_id", "eia_plant_name", "ercot_resource_name", "ercot_settlement_point", "county", "ac_mw_eia",
    "ac_mw_ercot", "cod_eia", "match_method", "match_confidence", "verified_by", "verified_on", "notes",
    "cdr_unit_name", "cdr_year", "name_score", "mw_error_pct", "sced_coverage", "sced_max_hsl_mw",
]
ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}


def norm_name(s, stopwords: set[str]) -> str:
    """'Taygete II Energy Project LLC' → 'taygete 2';  'TAYGETE_SLR_UNIT2' → 'taygete slr unit2'."""
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    words = re.sub(r"[^a-z0-9]+", " ", str(s).lower()).split()
    return " ".join(ROMAN.get(w, w) for w in words if w not in stopwords)


def norm_county(s) -> str:
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    return re.sub(r"\s+county$", "", str(s).strip().lower()).strip()


def _year(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return np.nan
    m = re.search(r"(19|20)\d\d", str(v))
    return float(m.group(0)) if m else np.nan


def cdr_units(raw: pd.DataFrame) -> pd.DataFrame:
    """CDR rows → unit_code, unit_name, county, mw, year (one row per unit code)."""
    code = find_col(raw, "Unit Code", required=True)
    name = find_col(raw, "Unit Name", "Resource Name", "Project Name", what="CDR unit name")
    county = find_col(raw, "County", required=True)
    mw = find_col(raw, "Installed Capacity Rating", "Installed Capacity", "Capacity (MW)", "Summer Capacity",
                  "Nameplate", what="CDR capacity")
    year = find_col(raw, "In Service Year", "In Service", "Year In Service", "Commercial Operation", what="CDR year")
    u = pd.DataFrame({
        "unit_code": raw[code].astype(str).str.strip(),
        "unit_name": raw[name] if name else None,
        "county_n": raw[county].map(norm_county),
        "county": raw[county],
        "mw": pd.to_numeric(raw[mw], errors="coerce") if mw else np.nan,
        "year": raw[year].map(_year) if year else np.nan,
    })
    return u.drop_duplicates("unit_code").reset_index(drop=True)


def _score_units(plant: pd.Series, units: pd.DataFrame, stop: set[str]) -> pd.DataFrame:
    n_eia = norm_name(plant.plant_name, stop)
    u = units.copy()
    u["name_score"] = [
        max(fuzz.token_set_ratio(n_eia, norm_name(nm, stop)) if n_eia and norm_name(nm, stop) else 0,
            fuzz.token_set_ratio(n_eia, norm_name(cd, stop)) if n_eia and norm_name(cd, stop) else 0)
        for nm, cd in zip(u["unit_name"], u["unit_code"])
    ]
    return u


def match_plant(plant: pd.Series, units: pd.DataFrame, c: dict) -> tuple[list[dict], str, str]:
    """→ (chosen unit rows, confidence, method)."""
    stop = set(c["stopwords"])
    in_county = units[units["county_n"] == norm_county(plant.county)]
    county_ok = not in_county.empty
    pool = _score_units(plant, in_county if county_ok else units, stop)
    c_year = c["year_tolerance"]
    pool["year_ok"] = [
        any(abs(y - t.year) <= c_year for t in (plant.cod_first, plant.cod_last) if pd.notna(t)) if pd.notna(y) else False
        for y in pool["year"]
    ]
    if not county_ok:
        pool = pool[pool["name_score"] >= c["name_high"]]  # outside the county only a strong name match counts
    chosen = pool[pool["name_score"] >= c["name_medium"]]
    method = "name+county" if county_ok else "name(no county match)"
    if chosen.empty and county_ok:
        # weak name: accept the single county unit whose MW and year agree (e.g. code-only CDR rows)
        mw_fit = (pool["mw"] - plant.ac_mw).abs() / plant.ac_mw <= c["mw_tolerance_high"]
        alt = pool[(pool["name_score"] >= c["name_min"]) | (mw_fit & pool["year_ok"])]
        alt = alt.assign(_mw_err=(alt["mw"] - plant.ac_mw).abs()).sort_values(["name_score", "_mw_err"],
                                                                               ascending=[False, True])
        chosen = alt.head(1)
        method = "mw+year+county"
    if chosen.empty:
        # nothing in the county: a strong name match elsewhere (county line, CDR/EIA county disagreement) → low
        far = _score_units(plant, units[units["county_n"] != norm_county(plant.county)], stop)
        chosen = far[far["name_score"] >= c["name_high"]].assign(year_ok=False)
        method, county_ok = "name(no county match)", False
    if chosen.empty:
        return [], "none", "no candidate"

    mw_sum = chosen["mw"].sum(min_count=1)
    mw_err = abs(mw_sum - plant.ac_mw) / plant.ac_mw if pd.notna(mw_sum) else np.inf
    best = chosen["name_score"].max()
    if county_ok and best >= c["name_high"] and mw_err <= c["mw_tolerance_high"]:
        conf = "high"
    elif county_ok and ((best >= c["name_medium"] and mw_err <= c["mw_tolerance_medium"])
                        or (chosen["year_ok"].all() and mw_err <= c["mw_tolerance_high"])):
        conf = "medium"
    else:
        conf = "low"
    rows = [
        {"unit_code": r.unit_code, "cdr_unit_name": r.unit_name, "ac_mw_ercot": r.mw, "cdr_year": r.year,
         "name_score": round(float(r.name_score), 1), "mw_error_pct": round(100 * mw_err, 1) if np.isfinite(mw_err) else None}
        for r in chosen.itertuples()
    ]
    return rows, conf, f"auto:{method}"


def settlement_points(mapping: pd.DataFrame | None) -> dict[str, str]:
    """resource name → resource node. ERCOT resource names are usually '<UNIT SUBSTATION>_<UNIT NAME>'."""
    if mapping is None or mapping.empty:
        return {}
    node = find_col(mapping, "Resource Node", required=True)
    sub = find_col(mapping, "Unit Substation", what="unit substation")
    unit = find_col(mapping, "Unit Name", required=True)
    subs = mapping[sub] if sub else [None] * len(mapping)
    out = {}
    for rn, sb, un in zip(mapping[node], subs, mapping[unit]):
        if not un or pd.isna(un):
            continue
        if sb and not pd.isna(sb):
            out.setdefault(f"{sb}_{un}".upper(), rn)   # exact '<substation>_<unit>' wins over bare unit name
    for rn, un in zip(mapping[node], mapping[unit]):
        if un and not pd.isna(un):
            out.setdefault(str(un).upper(), rn)
    return out


def build(plants: pd.DataFrame, cdr_raw: pd.DataFrame, sced: pd.DataFrame | None = None,
          mapping: pd.DataFrame | None = None, existing: pd.DataFrame | None = None,
          cfg: dict | None = None, today: dt.date | None = None) -> pd.DataFrame:
    cfg = cfg or load_config()
    c = cfg["crosswalk"]
    today = today or dt.date.today()
    units = cdr_units(cdr_raw)
    sced_idx = {} if sced is None else {str(n).upper(): h for n, h in zip(sced["resource_name"], sced["max_hsl_mw"])}
    nodes = settlement_points(mapping)

    existing = existing if existing is not None else pd.DataFrame(columns=CSV_COLUMNS)
    verified = existing[existing["verified_by"].fillna("").astype(str).str.strip() != ""]
    locked = set(pd.to_numeric(verified["eia_plant_id"], errors="coerce").dropna().astype(int))

    todo = plants[plants["filter_status"].isin(["pass", "review"]) & ~plants["eia_id"].isin(locked)]
    rows = []
    for p in todo.itertuples(index=False):
        chosen, conf, method = match_plant(pd.Series(p._asdict()), units, c)
        base = {
            "eia_plant_id": int(p.eia_id), "eia_plant_name": p.plant_name, "county": p.county,
            "ac_mw_eia": float(p.ac_mw), "cod_eia": p.cod_first.date().isoformat() if pd.notna(p.cod_first) else None,
            "match_method": method, "match_confidence": conf, "verified_by": None, "verified_on": None, "notes": None,
        }
        if not chosen:
            rows.append({**base, "ercot_resource_name": None})
            continue
        for ch in chosen:
            code = ch.pop("unit_code")
            in_sced = code.upper() in sced_idx if sced_idx else None
            rows.append({
                **base, **ch, "ercot_resource_name": code,
                "ercot_settlement_point": nodes.get(code.upper()),
                "sced_coverage": in_sced,
                "sced_max_hsl_mw": sced_idx.get(code.upper()) if sced_idx else None,
            })
    new = pd.DataFrame(rows, columns=CSV_COLUMNS)

    # one ERCOT unit claimed by two plants → both rows drop to low with a note
    claimed = new.dropna(subset=["ercot_resource_name"]).groupby("ercot_resource_name")["eia_plant_id"].nunique()
    for code in claimed[claimed > 1].index:
        m = new["ercot_resource_name"] == code
        others = sorted(new.loc[m, "eia_plant_id"].unique())
        new.loc[m, "match_confidence"] = "low"
        new.loc[m, "notes"] = f"unit claimed by EIA plants {others}"
    if sced_idx:
        miss = new["ercot_resource_name"].notna() & (new["sced_coverage"] == False)  # noqa: E712
        new.loc[miss, "notes"] = [f"{n}; not in SCED PVGR list" if isinstance(n, str) else "not in SCED PVGR list"
                                  for n in new.loc[miss, "notes"]]

    parts = [f for f in (verified.reindex(columns=CSV_COLUMNS), new) if not f.empty]
    out = pd.concat(parts, ignore_index=True) if parts else new
    return out.sort_values(["county", "eia_plant_id", "ercot_resource_name"], na_position="last").reset_index(drop=True)


def review_report(xw: pd.DataFrame, path: Path = REVIEW_MD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    per_plant = xw.groupby("eia_plant_id").agg(conf=("match_confidence", "first"),
                                               verified=("verified_by", lambda s: s.notna().any()))
    counts = per_plant["conf"].value_counts().reindex(["high", "medium", "low", "none"], fill_value=0)
    lines = [f"# EIA ↔ ERCOT crosswalk review — {dt.date.today():%Y-%m-%d}", "",
             f"Plants: {len(per_plant)} · verified: {int(per_plant['verified'].sum())} · "
             + " · ".join(f"{k}: {v}" for k, v in counts.items()), "",
             "To confirm a row, put your initials in `verified_by` and the date in `verified_on` in "
             "data/crosswalk_eia_ercot.csv (edit or delete wrong rows; add missing ones). Verified rows are never "
             "overwritten by later runs.", "",
             "| Conf | EIA ID | Plant | County | MW EIA | ERCOT resource | CDR name | MW ERCOT | CDR yr | Name | MW err | SCED | Node | Notes |",
             "|---|---:|---|---|---:|---|---|---:|---:|---:|---:|---|---|---|"]
    order = {"none": 0, "low": 1, "medium": 2, "high": 3}
    for r in xw.sort_values(by=["match_confidence", "county"], key=lambda s: s.map(order) if s.name == "match_confidence" else s).itertuples():
        def f(v, fmt="{}"):
            return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else fmt.format(v)
        lines.append("| " + " | ".join([
            f(r.match_confidence), str(r.eia_plant_id), f(r.eia_plant_name), f(r.county), f(r.ac_mw_eia, "{:.1f}"),
            f(r.ercot_resource_name), f(r.cdr_unit_name), f(r.ac_mw_ercot, "{:.1f}"), f(r.cdr_year, "{:.0f}"),
            f(r.name_score, "{:.0f}"), f(r.mw_error_pct, "{:.0f}%"),
            {True: "yes", False: "NO"}.get(r.sced_coverage, "—"), f(r.ercot_settlement_point), f(r.notes),
        ]) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path


def run() -> Path:
    plants = pd.read_parquet(DATA_DIR / "plants.parquet")
    cdr_path = raw_dir("ercot_cdr") / "cdr_units.parquet"
    if not cdr_path.exists():
        raise RuntimeError("run the ERCOT ingest first (python -m pipeline.run --phase 3): cdr_units.parquet missing")
    cdr_raw = pd.read_parquet(cdr_path)
    sp, mp = raw_dir("ercot") / "sced_pv_resources.parquet", raw_dir("ercot") / "resource_node_to_unit.parquet"
    sced = pd.read_parquet(sp) if sp.exists() else None
    mapping = pd.read_parquet(mp) if mp.exists() else None
    existing = pd.read_csv(CROSSWALK_CSV, dtype=str) if CROSSWALK_CSV.exists() and CROSSWALK_CSV.stat().st_size else None
    xw = build(plants, cdr_raw, sced, mapping, existing)
    xw.to_csv(CROSSWALK_CSV, index=False)
    md = review_report(xw)
    per_plant = xw.groupby("eia_plant_id")["match_confidence"].first().value_counts().to_dict()
    log.info("crosswalk: %d rows for %d plants %s → %s; review → %s",
             len(xw), xw["eia_plant_id"].nunique(), per_plant, CROSSWALK_CSV, md)
    return CROSSWALK_CSV
