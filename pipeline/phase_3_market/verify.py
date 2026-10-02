"""Record your crosswalk decisions in data/crosswalk_eia_ercot.csv (verified rows are never overwritten by reruns).

    python -m pipeline.phase_3_market.verify --initials CR --accept-high
    python -m pipeline.phase_3_market.verify --initials CR --accept-medium
    python -m pipeline.phase_3_market.verify --initials CR --accept 61368,62932
    python -m pipeline.phase_3_market.verify --initials CR --set 63255=EUNICE_PV1+EUNICE_PV2 --set 64447=WES_UNIT1+WES_UNIT2
    python -m pipeline.phase_3_market.verify --initials CR --no-resource 60774,63388 --note "behind the meter"
    python -m pipeline.phase_3_market.verify --status

--accept-high   every unverified 'high' row
--accept-medium every unverified 'medium' row (MW+year fallbacks and shared units — read the report first)
--accept IDS    every row of these EIA plant ids, as proposed
--set ID=A+B    replace a plant's rows with these ERCOT resource names (manual match)
--no-resource   record that these plants have no ERCOT resource (stops them being re-matched)
--undo IDS      clear verification for these plants (next run re-matches them)
"""
from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd

from pipeline.common import DATA_DIR
from pipeline.phase_3_market.crosswalk import CROSSWALK_CSV, CSV_COLUMNS


def _ids(s: str | None) -> set[str]:
    return {x.strip() for x in (s or "").split(",") if x.strip()}


def load() -> pd.DataFrame:
    return pd.read_csv(CROSSWALK_CSV, dtype=str).reindex(columns=CSV_COLUMNS)


def is_verified(x: pd.DataFrame) -> pd.Series:
    return x["verified_by"].fillna("").str.strip() != ""


def apply(x: pd.DataFrame, initials: str, today: str, accept_high=False, accept=(), sets=(), no_resource=(),
          note=None, undo=(), accept_medium=False) -> pd.DataFrame:
    x = x.copy()
    pid = x["eia_plant_id"].astype(str)
    stamp = {"verified_by": initials, "verified_on": today}
    levels = [lv for lv, on in (("high", accept_high), ("medium", accept_medium)) if on]
    if levels:
        m = x["match_confidence"].isin(levels) & x["ercot_resource_name"].notna() & ~is_verified(x)
        x.loc[m, list(stamp)] = list(stamp.values())
    for i in accept:
        m = pid == i
        if not m.any():
            raise SystemExit(f"EIA plant {i} is not in the crosswalk")
        x.loc[m, list(stamp)] = list(stamp.values())
    new_rows = []
    for spec in sets:
        i, codes = spec.split("=", 1)
        i = i.strip()
        base = x[pid == i]
        if base.empty:
            raise SystemExit(f"EIA plant {i} is not in the crosswalk")
        b = base.iloc[0].to_dict()
        for col in ("ercot_settlement_point", "ac_mw_ercot", "cdr_unit_name", "cdr_year", "name_score",
                    "mw_error_pct", "sced_coverage", "sced_max_hsl_mw", "notes"):
            b[col] = None
        for code in [c.strip() for c in codes.split("+") if c.strip()]:
            new_rows.append({**b, "ercot_resource_name": code, "match_method": "manual", "match_confidence": "high",
                             "notes": note or "manual match", **stamp})
        x, pid = x[pid != i], pid[pid != i]
    for i in no_resource:
        base = x[pid == i]
        if base.empty:
            raise SystemExit(f"EIA plant {i} is not in the crosswalk")
        b = {**base.iloc[0].to_dict(), "ercot_resource_name": None, "ercot_settlement_point": None,
             "match_method": "manual", "match_confidence": "none", "notes": note or "no ERCOT resource", **stamp}
        new_rows.append(b)
        x, pid = x[pid != i], pid[pid != i]
    for i in undo:
        x.loc[pid == i, ["verified_by", "verified_on"]] = None
    if new_rows:
        x = pd.concat([x, pd.DataFrame(new_rows, columns=CSV_COLUMNS)], ignore_index=True)
    return x.sort_values(["county", "eia_plant_id", "ercot_resource_name"], na_position="last").reset_index(drop=True)


def status(x: pd.DataFrame) -> str:
    v = is_verified(x)
    per = x.assign(v=v).groupby("eia_plant_id").agg(v=("v", "all"), conf=("match_confidence", "first"))
    lines = [f"crosswalk: {len(per)} plants, {int(per['v'].sum())} verified, {int((~per['v']).sum())} to review"]
    plants_pq = DATA_DIR / "plants.parquet"
    if plants_pq.exists():
        tiers = pd.read_parquet(plants_pq, columns=["eia_id", "tier", "filter_status"])
        tiers["eia_plant_id"] = tiers["eia_id"].astype(str)
        j = per.reset_index().merge(tiers, on="eia_plant_id", how="left")
        t12 = j[j["tier"].isin(["T1b", "T1a", "T2"]) & (j["filter_status"] == "pass")]
        if len(t12):
            pct = 100 * t12["v"].mean()
            lines.append(f"T1/T2 pass plants verified: {int(t12['v'].sum())} of {len(t12)} = {pct:.0f}% (target ≥ 95%)")
    todo = per[~per["v"]].reset_index()
    if len(todo):
        names = x.drop_duplicates("eia_plant_id").set_index("eia_plant_id")["eia_plant_name"]
        lines.append("still to review (id · confidence · name):")
        for r in todo.sort_values("conf").itertuples():
            lines.append(f"  {r.eia_plant_id} · {r.conf} · {names.get(r.eia_plant_id, '')}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pipeline.phase_3_market.verify", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--initials")
    ap.add_argument("--accept-high", action="store_true")
    ap.add_argument("--accept-medium", action="store_true")
    ap.add_argument("--accept")
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--no-resource")
    ap.add_argument("--note")
    ap.add_argument("--undo")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    x = load()
    changing = a.accept_high or a.accept_medium or a.accept or a.set or a.no_resource or a.undo
    if changing:
        if not a.initials:
            raise SystemExit("--initials is required when recording decisions")
        before = set(x.loc[is_verified(x), "eia_plant_id"].astype(str))
        x = apply(x, a.initials.strip().upper(), dt.date.today().isoformat(), a.accept_high, _ids(a.accept),
                  a.set, _ids(a.no_resource), a.note, _ids(a.undo), accept_medium=a.accept_medium)
        x.to_csv(CROSSWALK_CSV, index=False)
        print(f"saved {CROSSWALK_CSV}")
        newly = x[is_verified(x) & ~x["eia_plant_id"].astype(str).isin(before)]
        if len(newly):
            print(f"newly verified: {newly['eia_plant_id'].nunique()} plants / {len(newly)} rows")
            for r in newly.itertuples():
                mw = "" if pd.isna(r.ac_mw_ercot) else f" ({float(r.ac_mw_ercot):.1f} MW vs {float(r.ac_mw_eia):.1f})"
                print(f"  {r.eia_plant_id} {r.eia_plant_name} -> {r.ercot_resource_name}{mw} [{r.match_confidence}]")
        from pipeline.phase_3_market.crosswalk import REVIEW_MD, review_report

        print(f"report refreshed: {review_report(x, REVIEW_MD)}")
    print(status(x))


if __name__ == "__main__":
    main()
