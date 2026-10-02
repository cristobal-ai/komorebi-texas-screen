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


def _score_units(plant, units: pd.DataFrame, stop: set[str]) -> pd.DataFrame:
    n_eia = norm_name(plant.plant_name, stop)
    u = units.copy()
    u["name_score"] = [
        max(fuzz.token_set_ratio(n_eia, norm_name(nm, stop)) if n_eia and norm_name(nm, stop) else 0,
            fuzz.token_set_ratio(n_eia, norm_name(cd, stop)) if n_eia and norm_name(cd, stop) else 0)
        for nm, cd in zip(u["unit_name"], u["unit_code"])
    ]
    return u


def _digits(name, stop: set[str]) -> set[str]:
    """Phase numbers in a name: 'Prospero Solar II' → {'2'}; 'BNB LAMESA SOLAR (PHASE I)' → {'1'}; 'X SOLAR 2 U1' → {'2'}."""
    return {w for w in norm_name(name, stop).split() if w.isdigit()}


def _annotate(plant, cands: pd.DataFrame, c: dict) -> pd.DataFrame:
    stop = set(c["stopwords"])
    want = _digits(plant.plant_name, stop)
    cods = [t.year for t in (plant.cod_first, plant.cod_last) if pd.notna(t)]
    out = cands.copy()
    out["phase_ok"] = [
        bool(want & _digits(nm, stop)) if want else _digits(nm, stop) <= {"1"}
        for nm in out["unit_name"].fillna("")
    ]
    out["year_ok"] = [
        any(abs(y - t) <= c["year_tolerance"] for t in cods) if pd.notna(y) and cods else pd.isna(y)
        for y in out["year"]
    ]
    return out


def candidates(plant, units: pd.DataFrame, c: dict, taken: set[str]) -> tuple[pd.DataFrame, str, bool]:
    """Candidate units for one plant (excluding units already assigned) → (cands, method, county_ok)."""
    free = units[~units["unit_code"].isin(taken)]
    in_county = free[free["county_n"] == norm_county(plant.county)]
    county_ok = not units[units["county_n"] == norm_county(plant.county)].empty
    pool = _annotate(plant, _score_units(plant, in_county, set(c["stopwords"])), c)
    cands = pool[pool["name_score"] >= c["name_medium"]]
    if not cands.empty:
        return cands, "name+county", True
    if not pool.empty:
        # weak names (e.g. code-only CDR rows): a single county unit whose MW and year agree
        fit = ((pool["mw"] - plant.ac_mw).abs() / plant.ac_mw <= c["mw_tolerance_high"]) & pool["year_ok"]
        alt = pool[(pool["name_score"] >= c["name_min"]) | fit]
        if not alt.empty:
            alt = alt.assign(_e=(alt["mw"] - plant.ac_mw).abs()).sort_values(["name_score", "_e"], ascending=[False, True])
            return alt.head(1).drop(columns="_e"), "mw+year+county", True
    # nothing in the county: a strong name match elsewhere (county line, CDR/EIA county disagreement)
    far = _annotate(plant, _score_units(plant, free[free["county_n"] != norm_county(plant.county)],
                                        set(c["stopwords"])), c)
    return far[far["name_score"] >= c["name_high"]], "name(no county match)", False


def select_subset(plant, cands: pd.DataFrame, c: dict) -> tuple[pd.DataFrame, float]:
    """Subset of candidate units whose MW best sums to the plant's AC MW (with phase / year penalties)."""
    from itertools import combinations

    if cands.empty:
        return cands, np.inf
    top = cands.sort_values("name_score", ascending=False).head(c["max_candidates"]).reset_index(drop=True)
    if top["mw"].isna().all():
        keep = top[top["phase_ok"] & top["year_ok"]]
        return (keep if not keep.empty else top), np.inf
    best, best_obj, best_err = None, np.inf, np.inf
    known = top[top["mw"].notna()]
    for r in range(1, len(known) + 1):
        for idx in combinations(known.index, r):
            sub = known.loc[list(idx)]
            err = abs(sub["mw"].sum() - plant.ac_mw) / plant.ac_mw
            obj = (err + c["phase_penalty"] * (~sub["phase_ok"]).sum()
                   + c["year_penalty"] * (~sub["year_ok"]).sum())
            if obj < best_obj - 1e-9:
                best, best_obj, best_err = sub, obj, err
    return best, best_err


def confidence(chosen: pd.DataFrame, mw_err: float, county_ok: bool, c: dict) -> str:
    name = chosen["name_score"].max()
    if county_ok and name >= c["name_high"] and mw_err <= c["mw_tolerance_high"]:
        return "high"
    if county_ok and ((name >= c["name_medium"] and mw_err <= c["mw_tolerance_medium"])
                      or (chosen["year_ok"].all() and mw_err <= c["mw_tolerance_high"])):
        return "medium"
    return "low"


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
    units = cdr_units(cdr_raw)
    sced_idx = {} if sced is None else {str(n).upper(): h for n, h in zip(sced["resource_name"], sced["max_hsl_mw"])}
    nodes = settlement_points(mapping)

    existing = existing if existing is not None else pd.DataFrame(columns=CSV_COLUMNS)
    verified = existing[existing["verified_by"].fillna("").astype(str).str.strip() != ""]
    locked = set(pd.to_numeric(verified["eia_plant_id"], errors="coerce").dropna().astype(int))
    taken: dict[str, int] = {str(u): -1 for u in verified["ercot_resource_name"].dropna()}  # verified units stay put

    todo = plants[plants["filter_status"].isin(["pass", "review"]) & ~plants["eia_id"].isin(locked)]
    todo = [pd.Series(p._asdict()) for p in todo.itertuples(index=False)]
    # best-fitting plants claim their units first (Prospero 300 MW takes Prospero 1 before Prospero II looks)
    first = {}
    for p in todo:
        cands, _, _ = candidates(p, units, c, set())
        first[p.eia_id] = select_subset(p, cands, c)[1]
    todo.sort(key=lambda p: first[p.eia_id])

    picks: dict[int, tuple] = {}
    for p in todo:
        cands, method, county_ok = candidates(p, units, c, set(taken))
        chosen, err = select_subset(p, cands, c)
        if chosen is not None and not chosen.empty:
            picks[p.eia_id] = (chosen, err, county_ok, f"auto:{method}", None)
            for u in chosen["unit_code"]:
                taken[u] = int(p.eia_id)
            continue
        # nothing left: is this plant one of several EIA plants behind one ERCOT unit (Oberon IA + IB)?
        shared = None
        held = units[units["unit_code"].isin([u for u, q in taken.items() if q > 0])]
        held = _annotate(p, _score_units(p, held[held["county_n"] == norm_county(p.county)], set(c["stopwords"])), c)
        for u in held[held["name_score"] >= c["name_high"]].itertuples():
            q = taken[u.unit_code]
            q_mw = float(plants.loc[plants["eia_id"] == q, "ac_mw"].iloc[0])
            if pd.notna(u.mw) and abs(p.ac_mw + q_mw - u.mw) / u.mw <= c["mw_tolerance_medium"]:
                shared = (u, q, q_mw)
                break
        if shared:
            u, q, q_mw = shared
            note = f"one ERCOT unit shared by EIA plants {sorted([int(p.eia_id), int(q)])} ({p.ac_mw:g} + {q_mw:g} MW vs {u.mw:g} MW)"
            row = held[held["unit_code"] == u.unit_code]
            err = abs(p.ac_mw + q_mw - u.mw) / u.mw
            picks[p.eia_id] = (row, err, True, "auto:shared unit", note)
            qc, qe, qk, qm, qn = picks[q]
            picks[q] = (qc, err, qk, qm, note)
        else:
            picks[p.eia_id] = (None, np.inf, False, "no candidate", None)

    rows = []
    for p in todo:
        chosen, err, county_ok, method, note = picks[p.eia_id]
        base = {
            "eia_plant_id": int(p.eia_id), "eia_plant_name": p.plant_name, "county": p.county,
            "ac_mw_eia": float(p.ac_mw), "cod_eia": p.cod_first.date().isoformat() if pd.notna(p.cod_first) else None,
            "match_method": method, "verified_by": None, "verified_on": None, "notes": note,
        }
        if chosen is None or chosen.empty:
            rows.append({**base, "match_confidence": "none", "ercot_resource_name": None})
            continue
        conf = confidence(chosen, err, county_ok, c)
        if method == "auto:shared unit" or (note and "shared" in note):
            conf = "medium" if conf == "high" else conf
        for r in chosen.itertuples():
            code = r.unit_code
            in_sced = code.upper() in sced_idx if sced_idx else None
            n = note
            if sced_idx and not in_sced:
                n = f"{n}; not in SCED PVGR list" if n else "not in SCED PVGR list"
            rows.append({
                **base, "notes": n, "match_confidence": conf, "ercot_resource_name": code,
                "cdr_unit_name": r.unit_name, "ac_mw_ercot": r.mw, "cdr_year": r.year,
                "name_score": round(float(r.name_score), 1),
                "mw_error_pct": round(100 * err, 1) if np.isfinite(err) else None,
                "ercot_settlement_point": nodes.get(code.upper()),
                "sced_coverage": in_sced, "sced_max_hsl_mw": sced_idx.get(code.upper()) if sced_idx else None,
            })
    new = pd.DataFrame(rows, columns=CSV_COLUMNS)
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
             "This report is read-only. Record decisions with `python -m pipeline.phase_3_market.verify` "
             "(--accept-high, --accept IDS, --set ID=A+B, --no-resource IDS, --status); it writes `verified_by` / "
             "`verified_on` (columns 11–12) into data/crosswalk_eia_ercot.csv and rerun this report to see them in the "
             "Verified column. Verified rows are never overwritten by later runs.", "",
             "| Conf | Verified | EIA ID | Plant | County | MW EIA | ERCOT resource | CDR name | MW ERCOT | CDR yr | Name | MW err | SCED | Node | Notes |",
             "|---|---|---:|---|---|---:|---|---|---:|---:|---:|---:|---|---|---|"]
    order = {"none": 0, "low": 1, "medium": 2, "high": 3}
    for r in xw.sort_values(by=["match_confidence", "county"], key=lambda s: s.map(order) if s.name == "match_confidence" else s).itertuples():
        def f(v, fmt="{}"):
            return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else fmt.format(v)
        lines.append("| " + " | ".join([
            f(r.match_confidence),
            (f"{r.verified_by} {str(r.verified_on)[:10]}" if isinstance(r.verified_by, str) and r.verified_by.strip() else "—"),
            str(r.eia_plant_id), f(r.eia_plant_name), f(r.county), f(r.ac_mw_eia, "{:.1f}"),
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
