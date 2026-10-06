"""Section A offtake status (4 pts) from the hand-maintained data/offtake.csv.

No public dataset covers ERCOT offtake (FERC EQR misses ERCOT-only sellers), so the input is one sourced row per plant
from owner SEC filings, utility filings and PPA press releases, committed like the crosswalk.

Classes (brief §5A; owner decisions 6 Oct 2026 for the last four):
    merchant              4   stated merchant, or the contract has ended with no replacement found
    short_contract        3   PPA or hedge with < short_years left
    long_contract_ig      0   PPA or hedge with >= short_years left, investment-grade counterparty
    long_contract_non_ig  1   ... counterparty not investment grade (or unrated)
    long_contract_unknown_credit  config value (between the two)
    utility_owned         0   utility / municipal / coop self-supply (treated like a long IG contract)
    affiliate             3   offtake by the owner's own affiliate (generator-retailer, oil company, trading arm):
                              re-papered or ended in a sale, so close to merchant (Claude's criteria, owner-delegated)
    unknown               2   no row or offtake_type unknown (neutral, listed in missing_inputs)
Hedges (fixed-shape / proxy-revenue swaps) score like PPAs by years left: they still have to be unwound.
A contract whose end is not published gets COD + assumed_term_years[type] (end basis 'assumed', flagged).

Owner, 6 Oct 2026: "use your criteria; when in doubt flag it in the database" - doubts go to offtake_flags (rules in
classify() plus the table's free-text `flag` column) instead of a manual review.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.common import DATA_DIR

COLUMNS = ["eia_id", "plant_name", "offtake_type", "counterparty", "counterparty_ig", "contract_start", "contract_end",
           "contract_end_basis", "share_contracted", "source_url", "source_date", "confidence", "flag", "notes",
           "verified_by", "verified_on"]
TYPES = {"ppa", "hedge", "affiliate", "utility_owned", "merchant", "unknown"}
END_BASIS = {"published", "computed", "assumed"}
IG = {"yes", "no", "unknown"}
CONFIDENCE = {"high", "medium", "low"}


def table_path(spec: dict) -> Path:
    return DATA_DIR.parent / spec["table"]


def read_table(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False).replace({"": None})
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    df["eia_id"] = pd.to_numeric(df["eia_id"], errors="coerce").astype("Int64")
    df["share_contracted"] = pd.to_numeric(df["share_contracted"], errors="coerce")
    return df[COLUMNS]


def validate(df: pd.DataFrame) -> list[str]:
    errs = []
    dup = df["eia_id"][df["eia_id"].duplicated()].dropna().unique()
    if len(dup):
        errs.append(f"duplicate eia_id: {', '.join(map(str, dup))}")
    for i, r in df.iterrows():
        tag = f"{r['eia_id']} {r['plant_name'] or ''}".strip() if pd.notna(r["eia_id"]) else f"row {i + 2}"
        if pd.isna(r["eia_id"]):
            errs.append(f"{tag}: eia_id is blank")
        if r["offtake_type"] not in TYPES:
            errs.append(f"{tag}: offtake_type {r['offtake_type']!r} not in {sorted(TYPES)}")
        if r["counterparty_ig"] is not None and r["counterparty_ig"] not in IG:
            errs.append(f"{tag}: counterparty_ig {r['counterparty_ig']!r} not in {sorted(IG)}")
        if r["offtake_type"] != "unknown":
            if not r["source_url"]:
                errs.append(f"{tag}: source_url is blank")
            if r["confidence"] not in CONFIDENCE:
                errs.append(f"{tag}: confidence {r['confidence']!r} not in {sorted(CONFIDENCE)}")
        if r["contract_end_basis"] is not None and r["contract_end_basis"] not in END_BASIS:
            errs.append(f"{tag}: contract_end_basis {r['contract_end_basis']!r} not in {sorted(END_BASIS)}")
        if r["contract_end"] is not None and parse_end(r["contract_end"], 7) is None:
            errs.append(f"{tag}: contract_end {r['contract_end']!r} is not YYYY, YYYY-MM or YYYY-MM-DD")
    return errs


def parse_end(v, year_only_month: int) -> dt.date | None:
    """'2035' → 2035-<year_only_month>-01; '2035-06' → 2035-06-01; '2035-06-30' as is."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    try:
        if len(s) == 4:
            return dt.date(int(s), year_only_month, 1)
        if len(s) == 7:
            return dt.date(int(s[:4]), int(s[5:7]), 1)
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def classify(df: pd.DataFrame, spec: dict, asof: dt.date, cod: pd.Series | None = None) -> pd.DataFrame:
    """Per row of the table: offtake_status, years left, points (NaN = unknown → neutral) and offtake_flags.

    cod: eia_id → first COD, used to assume an end for contracts whose end is not published."""
    df = df.reset_index(drop=True)
    t = df["offtake_type"]
    contract = t.isin(["ppa", "hedge"])
    end_txt = df["contract_end"].copy()
    basis = df["contract_end_basis"].where(df["contract_end_basis"].notna(),
                                           pd.Series(np.where(end_txt.notna(), "published", None), index=df.index))
    if cod is not None:
        c = df["eia_id"].map(lambda i: cod.get(i)).map(lambda v: None if v is None or pd.isna(v) else pd.Timestamp(v))
        terms = t.map(spec["assumed_term_years"]).astype(float)
        assume = contract & end_txt.isna() & c.notna() & terms.notna()
        end_txt = end_txt.where(~assume, pd.Series([f"{x.year + int(n)}-{x.month:02d}" if a else None
                                                    for x, n, a in zip(c, terms.fillna(0), assume)], index=df.index))
        basis = basis.where(~assume, "assumed")
    end = end_txt.map(lambda v: parse_end(v, spec["year_only_end_month"]))
    years = end.map(lambda d: None if d is None else round((d - asof).days / 365.25, 2)).astype(float)
    ig = df["counterparty_ig"].fillna("unknown")
    short = spec["short_years"]
    status = np.select(
        [t.eq("utility_owned"), t.eq("affiliate"), t.eq("merchant"),
         contract & years.le(0), contract & years.lt(short),
         contract & years.ge(short) & ig.eq("yes"), contract & years.ge(short) & ig.eq("no"), contract & years.ge(short)],
        ["utility_owned", "affiliate", "merchant", "merchant", "short_contract", "long_contract_ig",
         "long_contract_non_ig", "long_contract_unknown_credit"],
        default="unknown")
    pts = pd.Series(status).map({"utility_owned": spec["utility_owned"], "affiliate": spec["affiliate"],
                                 "merchant": spec["merchant"], "short_contract": spec["short_ppa_lt_5yr"],
                                 "long_contract_ig": spec["long_ig_ppa"], "long_contract_non_ig": spec["long_non_ig_ppa"],
                                 "long_contract_unknown_credit": spec["long_unknown_credit"]}).astype(float)
    expired = contract & years.le(0)

    # flags: every doubt a person would otherwise have had to check
    share = df["share_contracted"]
    cp = df["counterparty"].fillna("")
    near = spec["flag_years_near_line"]
    flags = []
    for i in range(len(df)):
        f = []
        if t[i] == "unknown":
            f.append("no offtake found in public sources")
        if basis[i] == "assumed":
            f.append(f"end not published: assumed COD + {int(spec['assumed_term_years'][t[i]])} yr")
        if t[i] == "affiliate":
            f.append("affiliate offtake: priced in a sale, not a third-party contract")
        if contract[i] and pd.notna(share[i]) and share[i] < spec["flag_share_below"]:
            f.append(f"only {share[i]:.0%} of output publicly contracted")
        if contract[i] and re.search(r"unnamed|undisclosed", cp[i], re.I):
            f.append("counterparty not named")
        if contract[i] and pd.notna(years[i]) and short <= years[i] < short + near:
            f.append(f"crosses the {short}-yr line within {near} yr")
        if bool(expired[i]):
            f.append("contract ended: scored merchant, a replacement may exist")
        if df["confidence"][i] == "low" and t[i] != "unknown":
            f.append("low-confidence source")
        if df["flag"][i]:
            f.append(df["flag"][i])
        flags.append("; ".join(f) or None)
    return pd.DataFrame({
        "eia_id": df["eia_id"].astype("int64").to_numpy(),
        "offtake_status": status,
        "offtake_type": t.to_numpy(),
        "offtake_counterparty": df["counterparty"].to_numpy(),
        "offtake_counterparty_ig": df["counterparty_ig"].to_numpy(),
        "offtake_contract_end": end_txt.to_numpy(),
        "offtake_end_basis": basis.to_numpy(),
        "offtake_years_left": years.to_numpy(),
        "offtake_expired": expired.to_numpy(),
        "offtake_share_contracted": share.to_numpy(),
        "offtake_source_url": df["source_url"].to_numpy(),
        "offtake_source_date": df["source_date"].to_numpy(),
        "offtake_row_confidence": df["confidence"].to_numpy(),
        "offtake_flags": flags,
        "offtake_pts": pts.to_numpy(),
    })


def load(spec: dict, asof: dt.date, cod: pd.Series | None = None) -> pd.DataFrame | None:
    """Classified table, or None when data/offtake.csv does not exist yet (offtake then stays neutral)."""
    path = table_path(spec)
    if not path.exists():
        return None
    df = read_table(path)
    errs = validate(df)
    if errs:
        raise SystemExit("offtake.csv has problems:\n  " + "\n  ".join(errs))
    return classify(df, spec, asof, cod)
