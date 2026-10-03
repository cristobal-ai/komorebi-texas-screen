"""Phase 3b: curtailment % and capture rate per plant, from SCED PV resource data and 15-minute real-time SPP.

Pure functions (no network); `pipeline/phase_1_ingest/ercot_history.py` fetches and caches the inputs.

    sced_to_15min(day_df)                  one operating day of SCED rows → per resource, per 15-min interval
    unit_monthly(sced15, spp15, xwalk)     per (plant, unit, month) sums
    plant_monthly(unit_monthly)            per (plant, month) sums + ratios   → plant_metrics_monthly
    plant_summary(plant_monthly, ...)      per plant over the window          → plant_metrics

Definitions (config.yaml → market_metrics):
    curtailment_pct = Σ max(HSL − Base Point, 0)·Δt / Σ HSL·Δt
    capture_rate    = (Σ gen·SPP_node / Σ gen) / mean(SPP_hub)   over the plant's own SCED intervals
    shape_capture   = (Σ gen·SPP_hub / Σ gen) / mean(SPP_hub)
    basis_ratio     = Σ gen·SPP_node / Σ gen·SPP_hub             (capture_rate = shape_capture × basis_ratio)

A low capture rate or high curtailment is a price signal that scores higher (CLAUDE.md thesis inversion): nothing
here filters on it. A plant with no ERCOT resource (behind the meter) gets `metrics_status = 'no_ercot_resource'`,
never a zero.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from pipeline.common import find_col, load_config

log = logging.getLogger(__name__)

TZ = "US/Central"
SUM_COLS = ["gen_mwh", "hsl_mwh", "curtailed_mwh", "gen_priced_mwh", "rev_node", "rev_hub", "hub_sum", "hub_n"]


def sced_to_15min(gen: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """PVGR rows of one SCED day → per resource, per 15-minute settlement interval (UTC start).

    Columns out: resource_name, interval_start (UTC), gen_mwh, hsl_mwh, curtailed_mwh, hours.
    SCED timestamps are Central clock time; the Repeated Hour Flag (Y on the second pass of the fall-back hour)
    resolves the ambiguous hour. Rows that stay ambiguous or non-existent are dropped and logged.
    """
    c = (cfg or load_config())
    mm, ec = c["market_metrics"], c["sources"]["ercot"]
    name = find_col(gen, "Resource Name", required=True)
    rtype = find_col(gen, "Resource Type", required=True)
    ts = find_col(gen, "SCED Time Stamp", "SCED Timestamp", required=True)
    bp = find_col(gen, "Base Point", required=True)
    tno = find_col(gen, "Telemetered Net Output", required=True)
    hsl = find_col(gen, "HSL", required=True)
    rep = find_col(gen, "Repeated Hour Flag")

    g = gen[gen[rtype].astype(str).str.strip().str.upper() == ec["sced_pv_resource_type"]].copy()
    cols = ["resource_name", "interval_start", "gen_mwh", "hsl_mwh", "curtailed_mwh", "hours"]
    if g.empty:
        return pd.DataFrame(columns=cols)
    g["resource_name"] = g[name].astype(str).str.strip()
    g["_ts"] = pd.to_datetime(g[ts], errors="coerce")
    for src, dst in ((bp, "_bp"), (tno, "_tno"), (hsl, "_hsl")):
        g[dst] = pd.to_numeric(g[src], errors="coerce")
    g = g.dropna(subset=["_ts"])

    if g["_ts"].dt.tz is not None:
        # gridstatus may already hand back tz-aware stamps; they are unambiguous, so convert rather than localize
        local = g["_ts"].dt.tz_convert("UTC")
    else:
        if rep:
            second_pass = g[rep].astype(str).str.strip().str.upper().eq("Y").to_numpy()
            amb: object = ~second_pass          # True = DST (first pass), False = standard time (second pass)
        else:
            amb = "NaT"
        local = g["_ts"].dt.tz_localize(TZ, ambiguous=amb, nonexistent="NaT")
    bad = local.isna()
    if bad.any():
        log.warning("SCED: dropped %d rows with ambiguous/non-existent local time", int(bad.sum()))
    g = g.assign(_utc=local.dt.tz_convert("UTC"))[~bad.to_numpy()].sort_values(["resource_name", "_utc"])

    # Δt: time to the resource's next SCED run; a gap above max_interval is not extrapolated (default length).
    default_h = mm["sced_interval_minutes_default"] / 60
    max_h = mm["max_interval_minutes"] / 60
    nxt = g.groupby("resource_name")["_utc"].shift(-1)
    dt_h = ((nxt - g["_utc"]).dt.total_seconds() / 3600).where(lambda s: (s > 0) & (s <= max_h)).fillna(default_h)

    hsl_v = g["_hsl"].clip(lower=0).fillna(0.0)
    g["gen_mwh"] = g["_tno"].clip(lower=0).fillna(0.0) * dt_h
    g["hsl_mwh"] = hsl_v * dt_h
    g["curtailed_mwh"] = (hsl_v - g["_bp"].fillna(hsl_v)).clip(lower=0) * dt_h   # unknown Base Point → no curtailment
    g["hours"] = dt_h
    g["interval_start"] = g["_utc"].dt.floor("15min")
    out = g.groupby(["resource_name", "interval_start"], as_index=False)[
        ["gen_mwh", "hsl_mwh", "curtailed_mwh", "hours"]].sum()
    return out[cols]


def _price_series(spp: pd.DataFrame, location: str) -> pd.Series:
    s = spp[spp["location"] == location]
    return s.drop_duplicates("interval_start").set_index("interval_start")["spp"]


def unit_monthly(sced15: pd.DataFrame, spp15: pd.DataFrame, xwalk: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """Per (eia_id, resource, month): generation, HSL, curtailment, price-weighted sums, days with data.

    xwalk: rows with eia_plant_id, ercot_resource_name, ercot_settlement_point (blank resources are skipped).
    spp15: interval_start (UTC), location, spp.
    """
    mm = (cfg or load_config())["market_metrics"]
    hub = _price_series(spp15, mm["hub_reference"])
    if hub.empty:
        log.warning("no %s prices — capture_rate will be empty", mm["hub_reference"])
    rows = []
    for x in xwalk.itertuples(index=False):
        res = x.ercot_resource_name
        if not isinstance(res, str) or not res.strip():
            continue
        u = sced15[sced15["resource_name"] == res.strip()].copy()
        if u.empty:
            continue
        sp = x.ercot_settlement_point if isinstance(x.ercot_settlement_point, str) and x.ercot_settlement_point.strip() else None
        node = _price_series(spp15, sp) if sp else pd.Series(dtype=float)
        u["hub"] = u["interval_start"].map(hub)
        u["node"] = u["interval_start"].map(node) if len(node) else np.nan
        local = u["interval_start"].dt.tz_convert(TZ)
        u["month"] = local.dt.strftime("%Y-%m")
        u["date"] = local.dt.date
        priced = u["node"].notna() & u["hub"].notna()
        u["gen_priced_mwh"] = u["gen_mwh"].where(priced, 0.0)
        u["rev_node"] = (u["gen_mwh"] * u["node"]).where(priced, 0.0)
        u["rev_hub"] = (u["gen_mwh"] * u["hub"]).where(priced, 0.0)
        u["hub_sum"] = u["hub"].fillna(0.0)
        u["hub_n"] = u["hub"].notna().astype(int)
        g = u.groupby("month").agg(
            gen_mwh=("gen_mwh", "sum"), hsl_mwh=("hsl_mwh", "sum"), curtailed_mwh=("curtailed_mwh", "sum"),
            gen_priced_mwh=("gen_priced_mwh", "sum"), rev_node=("rev_node", "sum"), rev_hub=("rev_hub", "sum"),
            hub_sum=("hub_sum", "sum"), hub_n=("hub_n", "sum"), hours=("hours", "sum"),
            days=("date", "nunique"),
        ).reset_index()
        g.insert(0, "resource_name", res.strip())
        g.insert(0, "eia_id", int(x.eia_plant_id))
        g["settlement_point"] = sp
        g["node_priced"] = g["gen_priced_mwh"] > 0
        rows.append(g)
    if not rows:
        return pd.DataFrame(columns=["eia_id", "resource_name", "month", *SUM_COLS, "hours", "days", "settlement_point"])
    return pd.concat(rows, ignore_index=True)


def _ratio(num, den):
    return num / den if den and den > 0 else np.nan


def plant_monthly(units: pd.DataFrame, ac_mw: pd.Series, cfg: dict | None = None) -> pd.DataFrame:
    """Sum units into plants per month and attach the ratios. ac_mw: plant AC MW indexed by eia_id.

    A unit shared by two plants (Oberon IA/IB) is counted for each plant; ratios are unaffected, MWh are not
    additive across those plants — `plant_summary` flags it.
    """
    if units.empty:
        return pd.DataFrame()
    keys = ["eia_id", "month"]
    g = units.groupby(keys).agg(**{c: (c, "sum") for c in SUM_COLS}, hours=("hours", "max"), days=("days", "max"),
                                n_units=("resource_name", "nunique")).reset_index()
    hub_avg = g["hub_sum"] / g["hub_n"].replace(0, np.nan)
    g["hub_avg_spp"] = hub_avg
    node_w = g["rev_node"] / g["gen_priced_mwh"].replace(0, np.nan)
    hub_w = g["rev_hub"] / g["gen_priced_mwh"].replace(0, np.nan)
    g["node_gen_wtd_spp"], g["hub_gen_wtd_spp"] = node_w, hub_w
    g["curtailment_pct"] = g["curtailed_mwh"] / g["hsl_mwh"].replace(0, np.nan)
    g["capture_rate"] = node_w / hub_avg
    g["shape_capture"] = hub_w / hub_avg
    g["basis_ratio"] = g["rev_node"] / g["rev_hub"].replace(0, np.nan)
    ac = g["eia_id"].map(ac_mw)
    g["sced_net_cf"] = g["gen_mwh"] / (ac * g["hours"].replace(0, np.nan))
    g["sced_potential_cf"] = g["hsl_mwh"] / (ac * g["hours"].replace(0, np.nan))
    return g


def _days_in_month(month: str) -> int:
    return pd.Period(month).days_in_month


def plant_summary(monthly: pd.DataFrame, xwalk: pd.DataFrame, ac_mw: pd.Series, cfg: dict | None = None) -> pd.DataFrame:
    """One row per plant in the crosswalk (so plants without an ERCOT resource stay visible, with a status)."""
    mm = (cfg or load_config())["market_metrics"]
    x = xwalk.copy()
    x["eia_id"] = x["eia_plant_id"].astype(int)
    has_res = x["ercot_resource_name"].map(lambda v: isinstance(v, str) and bool(v.strip()))
    shared = set(x.loc[has_res & x["ercot_resource_name"].duplicated(keep=False), "eia_id"])
    meta = x.groupby("eia_id").agg(
        resources=("ercot_resource_name", lambda s: "+".join(v for v in s if isinstance(v, str) and v.strip())),
        settlement_points=("ercot_settlement_point", lambda s: "+".join(sorted({v for v in s if isinstance(v, str) and v.strip()}))),
    ).reset_index()
    out = meta.copy()
    out["ercot_resource_shared"] = out["eia_id"].isin(shared)

    if monthly is not None and len(monthly):
        m = monthly.copy()
        m["month_days"] = m["month"].map(_days_in_month)
        m["coverage"] = m["days"] / m["month_days"]
        full = m[m["coverage"] >= mm["min_month_coverage"]]
        agg = full.groupby("eia_id").agg(
            metrics_window_months=("month", "nunique"), window_start=("month", "min"), window_end=("month", "max"),
            gen_mwh=("gen_mwh", "sum"), hsl_mwh=("hsl_mwh", "sum"), curtailed_mwh=("curtailed_mwh", "sum"),
            gen_priced_mwh=("gen_priced_mwh", "sum"), rev_node=("rev_node", "sum"), rev_hub=("rev_hub", "sum"),
            hub_sum=("hub_sum", "sum"), hub_n=("hub_n", "sum"), hours=("hours", "sum"),
        ).reset_index()
        seen = m.groupby("eia_id")["month"].nunique().rename("months_seen").reset_index()
        out = out.merge(agg, on="eia_id", how="left").merge(seen, on="eia_id", how="left")
    else:
        out["metrics_window_months"] = np.nan

    for c in ["metrics_window_months", "window_start", "window_end", "gen_mwh", "hsl_mwh", "curtailed_mwh",
              "gen_priced_mwh", "rev_node", "rev_hub", "hub_sum", "hub_n", "hours", "months_seen"]:
        if c not in out:
            out[c] = np.nan
    ac = out["eia_id"].map(ac_mw)
    hub_avg = out["hub_sum"] / out["hub_n"].replace(0, np.nan)
    out["curtailment_pct"] = out["curtailed_mwh"] / out["hsl_mwh"].replace(0, np.nan)
    enough = out["gen_priced_mwh"] >= mm["min_gen_mwh_for_capture"]
    out["hub_avg_spp"] = hub_avg
    out["node_gen_wtd_spp"] = (out["rev_node"] / out["gen_priced_mwh"].replace(0, np.nan)).where(enough)
    out["capture_rate"] = (out["node_gen_wtd_spp"] / hub_avg).where(enough)
    out["shape_capture"] = (out["rev_hub"] / out["gen_priced_mwh"].replace(0, np.nan) / hub_avg).where(enough)
    out["basis_ratio"] = (out["rev_node"] / out["rev_hub"].replace(0, np.nan)).where(enough)
    out["sced_net_cf"] = out["gen_mwh"] / (ac * out["hours"].replace(0, np.nan))
    out["sced_potential_cf"] = out["hsl_mwh"] / (ac * out["hours"].replace(0, np.nan))

    has_sp = out["settlement_points"].astype(bool)
    has_win = out["metrics_window_months"].fillna(0) > 0
    out["metrics_status"] = np.select(
        [out["resources"] == "", ~out["eia_id"].isin(monthly["eia_id"] if monthly is not None and len(monthly) else []),
         ~has_win, ~has_sp, ~enough],
        ["no_ercot_resource", "no_sced_data", "no_full_months", "no_settlement_point", "too_little_generation"],
        default="ok",
    )
    # curtailment needs only SCED; flag when capture is missing for a reason other than missing SCED
    out["sced_coverage"] = np.select(
        [out["metrics_status"] == "no_ercot_resource", out["metrics_status"].isin(["no_sced_data", "no_full_months"])],
        ["none", "none"], default="full")
    out["metrics_window_months"] = out["metrics_window_months"].fillna(0).astype(int)
    return out.drop(columns=["gen_priced_mwh", "rev_node", "rev_hub", "hub_sum", "hub_n", "hours"])
