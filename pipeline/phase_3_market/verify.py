"""Record your crosswalk decisions in data/crosswalk_eia_ercot.csv (verified rows are never overwritten by reruns).

    python -m pipeline.phase_3_market.verify --initials CR --accept-high
    python -m pipeline.phase_3_market.verify --initials CR --accept-medium
    python -m pipeline.phase_3_market.verify --initials CR --accept 61368,62932
    python -m pipeline.phase_3_market.verify --initials CR --set 63255=EUNICE_PV1+EUNICE_PV2 --set 64447=WES_UNIT1+WES_UNIT2
    python -m pipeline.phase_3_market.verify --initials CR --no-resource 60774,63388 --note "behind the meter"
    python -m pipeline.phase_3_market.verify --status
    python -m pipeline.phase_3_market.verify --report

--accept-high   every unverified 'high' row
--accept-medium every unverified 'medium' row (MW+year fallbacks and shared units — read the report first)
--accept IDS    every row of these EIA plant ids, as proposed
--set ID=A+B    replace a plant's rows with these ERCOT resource names (manual match)
--no-resource   record that these plants have no ERCOT resource (stops them being re-matched)
--undo IDS      clear verification for these plants (next run re-matches them)
--candidates IDS  for these EIA plant ids, list every CDR unit that could belong to them (same county, or a name that
                 looks alike), with MW, in-service year, whether another plant already holds it, the SCED peak HSL and the
                 resource node, plus the MW sum of each plausible set. Use it before --set.
--report        regenerate data/validation/crosswalk_review.md from the CSV (no changes)
"""
from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd

from pipeline.common import DATA_DIR
from pipeline.phase_3_market.crosswalk import CROSSWALK_CSV, CSV_COLUMNS


def candidate_table(plant: dict, units: pd.DataFrame, xw: pd.DataFrame, sced: pd.DataFrame | None,
                    nodes: dict[str, str], stop: set[str]) -> str:
    """Text table of CDR units that could belong to one EIA plant (see --candidates)."""
    from pipeline.phase_3_market.crosswalk import _score_units, norm_county

    class P:  # _score_units only needs plant_name
        plant_name = plant["plant_name"]

    u = _score_units(P, units, stop)
    same = u["county_n"] == norm_county(plant["county"])
    u = u[same | (u["name_score"] >= 50)].copy()
    held = xw[xw["ercot_resource_name"].notna()].groupby("ercot_resource_name")["eia_plant_name"].first().to_dict()
    peak = {} if sced is None or sced.empty else dict(zip(sced["resource_name"].astype(str), sced["max_hsl_mw"]))
    u["held_by"] = u["unit_code"].map(held)
    u["sced_peak_mw"] = u["unit_code"].map(peak)
    u["node"] = u["unit_code"].map(lambda c: nodes.get(str(c).upper()))
    u["county_match"] = same[u.index]
    u = u.sort_values(["name_score", "county_match", "mw"], ascending=[False, False, False])
    lines = [f"\n== {plant['eia_id']} {plant['plant_name']}  ({plant['county']} County, {plant['ac_mw']:.1f} MW AC, "
             f"COD {plant.get('cod_first')}) =="]
    if u.empty:
        lines.append("  no CDR unit in the county and none with a similar name")
        return "\n".join(lines)
    lines.append(f"  {'unit_code':<22}{'cdr name':<34}{'county':<12}{'MW':>7}{'year':>6}{'name':>6}  {'peak HSL':>8}  held by / node")
    for r in u.itertuples():
        mw = "" if pd.isna(r.mw) else f"{r.mw:.1f}"
        yr = "" if pd.isna(r.year) else f"{int(r.year)}"
        pk = "" if pd.isna(r.sced_peak_mw) else f"{r.sced_peak_mw:.1f}"
        mine = isinstance(r.held_by, str) and r.held_by == plant["plant_name"]
        tag = ("current match" + (f" · {r.node}" if r.node else "")) if mine else (
            f"HELD: {r.held_by}" if isinstance(r.held_by, str) else (r.node or ""))
        lines.append(f"  {str(r.unit_code):<22}{str(r.unit_name)[:32]:<34}{str(r.county)[:11]:<12}{mw:>7}{yr:>6}"
                     f"{int(r.name_score):>6}  {pk:>8}  {tag}")
    free = u[u["held_by"].map(lambda v: not isinstance(v, str) or v == plant["plant_name"]) & u["mw"].notna()]
    from itertools import combinations
    best = []
    for k in range(1, min(5, len(free)) + 1):
        for combo in combinations(free.index, k):
            tot = free.loc[list(combo), "mw"].sum()
            err = abs(tot - plant["ac_mw"]) / plant["ac_mw"]
            if err <= 0.15:
                best.append((err, tot, sorted(free.loc[i, "unit_code"] for i in combo)))
    best.sort(key=lambda t: (t[0], len(t[2])))
    lines.append("  free-unit sets within 15% of the plant's MW:")
    lines += [f"    {tot:7.1f} MW ({err:.1%})  {'+'.join(codes)}" for err, tot, codes in best[:6]] or ["    none"]
    return "\n".join(lines)


def candidates_report(ids: set[str]) -> str:
    from pipeline.common import load_config, raw_dir
    from pipeline.phase_3_market.crosswalk import cdr_units, settlement_points

    plants = pd.read_parquet(DATA_DIR / "plants.parquet")
    cdr = cdr_units(pd.read_parquet(raw_dir("ercot_cdr") / "cdr_units.parquet"))
    sp, mp = raw_dir("ercot") / "sced_pv_resources.parquet", raw_dir("ercot") / "resource_node_to_unit.parquet"
    sced = pd.read_parquet(sp) if sp.exists() else None
    nodes = settlement_points(pd.read_parquet(mp)) if mp.exists() else {}
    stop = set(load_config()["crosswalk"]["stopwords"])
    xw = load()
    out = []
    for i in sorted(ids):
        row = plants[plants["eia_id"].astype(str) == i]
        if row.empty:
            out.append(f"\nEIA plant {i} is not in data/plants.parquet")
            continue
        r = row.iloc[0]
        out.append(candidate_table({"eia_id": i, "plant_name": r["plant_name"], "county": r["county"],
                                    "ac_mw": float(r["ac_mw"]), "cod_first": str(r.get("cod_first"))[:7]},
                                   cdr, xw, sced, nodes, stop))
    return "\n".join(out)


def _ids(s: str | None) -> set[str]:
    return {x.strip() for x in (s or "").split(",") if x.strip()}


def load() -> pd.DataFrame:
    return pd.read_csv(CROSSWALK_CSV, dtype=str).reindex(columns=CSV_COLUMNS)


def is_verified(x: pd.DataFrame) -> pd.Series:
    return x["verified_by"].fillna("").str.strip() != ""


def apply(x: pd.DataFrame, initials: str, today: str, accept_high=False, accept=(), sets=(), no_resource=(),
          note=None, undo=(), accept_medium=False, set_nodes=()) -> pd.DataFrame:
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
    for spec in set_nodes:
        res, node = (v.strip() for v in spec.split("=", 1))
        m = x["ercot_resource_name"] == res
        if not m.any():
            raise SystemExit(f"resource {res} is not in the crosswalk")
        x.loc[m, "ercot_settlement_point"] = node
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
    ap.add_argument("--set-node", action="append", default=[], metavar="RESOURCE=NODE",
                    help="set the settlement point of an ERCOT resource (find names with ercot_history --find)")
    ap.add_argument("--candidates", help="EIA plant ids: list possible CDR units (see module docstring)")
    ap.add_argument("--note")
    ap.add_argument("--undo")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args(argv)
    if a.candidates:
        print(candidates_report(_ids(a.candidates)))
        return
    x = load()
    changing = a.accept_high or a.accept_medium or a.accept or a.set or a.no_resource or a.undo or a.set_node
    if changing:
        if not a.initials:
            raise SystemExit("--initials is required when recording decisions")
        before = set(x.loc[is_verified(x), "eia_plant_id"].astype(str))
        x = apply(x, a.initials.strip().upper(), dt.date.today().isoformat(), a.accept_high, _ids(a.accept),
                  a.set, _ids(a.no_resource), a.note, _ids(a.undo), accept_medium=a.accept_medium, set_nodes=a.set_node)
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
    elif a.report:
        from pipeline.phase_3_market.crosswalk import REVIEW_MD, review_report

        print(f"report refreshed: {review_report(x, REVIEW_MD)}")
    print(status(x))


if __name__ == "__main__":
    main()
