"""Phase 5: score every pass/review plant on the brief's §5 model, as stored sub-scores the web app can re-weight.

Inputs: data/plants.parquet, data/plant_metrics.parquet (Phase 3b), data/layers/<name>.parquet (Phase 4).
Output: data/scores.parquet (one row per eia_id) and data/validation/score_report.md.

Rules (config.yaml → scoring; owner decisions 5 Oct 2026):
  * every component is stored as pts_<component>; sections score_A..score_F are their sums; score_total = A+B+C+D+E+F.
  * an input that is missing, or whose layer is not built yet, scores missing_share × max (neutral) and is listed in
    missing_inputs; data_completeness = share of the 100 positive points that came from real data.
  * flood_flag, distribution_class_poi, sb6_review_required are carried for display; they do not change the score.
  * poor generation performance scores HIGHER (thesis inversion): low capture, high curtailment, CF below benchmark.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.common import DATA_DIR, load_config

log = logging.getLogger(__name__)
LAYERS = ("transmission", "parcels", "flood", "gas_pipelines")

# component → (section, config path to its max points). E/F and not-yet-built inputs are handled below.
SECTIONS = {
    "A": ["capture", "curtailment", "cf_benchmark", "offtake"],
    "B": ["poly", "monofacial", "tracker"],
    "C": ["acres", "headroom", "land_control"],
    "D": ["poi_kv", "dist_345", "load_pocket"],
    "E": ["lambda", "drill", "hours25", "water"],
    "F": ["fixed_cost"],
}


# ---- band helpers -----------------------------------------------------------------------------------------------------
def band(value, bands: list[dict]) -> float:
    """First matching band's pts. Keys: lt, gt, gte, lte; {'else': pts}. NaN → NaN (the caller decides)."""
    if value is None or (isinstance(value, float) and np.isnan(value)) or pd.isna(value):
        return np.nan
    for b in bands:
        if "else" in b:
            return float(b["else"])
        if ("lt" in b and value < b["lt"]) or ("gt" in b and value > b["gt"]) or \
           ("gte" in b and value >= b["gte"]) or ("lte" in b and value <= b["lte"]):
            return float(b["pts"])
    return 0.0


def quantile_points(values: pd.Series, fleet: pd.Series, spec: dict, higher_is_better_for_seller: bool) -> pd.Series:
    """Quantile bands from the fleet distribution.

    capture (low = discounted asset = more points): spec {q25: 12, q50: 8, q75: 4, else: 0} → value <= q25 gets 12 ...
    curtailment (high = more points):               spec {q75: 8, q50: 5, else: 2}         → value >= q75 gets 8 ...
    """
    qs = {k: fleet.quantile(int(k[1:]) / 100) for k in spec if k.startswith("q")}
    order = sorted(qs, key=lambda k: int(k[1:]), reverse=higher_is_better_for_seller)

    def pts(v):
        if pd.isna(v):
            return np.nan
        for k in order:
            if (v >= qs[k]) if higher_is_better_for_seller else (v <= qs[k]):
                return float(spec[k])
        return float(spec["else"])

    return values.map(pts)


def region_of(lat, lon, regions: dict) -> str:
    for name, r in regions.items():
        ok = True
        if "lon_lt" in r: ok &= lon < r["lon_lt"]
        if "lon_gt" in r: ok &= lon > r["lon_gt"]
        if "lat_lt" in r: ok &= lat < r["lat_lt"]
        if "lat_gt" in r: ok &= lat > r["lat_gt"]
        if ok:
            return name
    return "central_texas"


# ---- inputs -----------------------------------------------------------------------------------------------------------
def assemble(plants: pd.DataFrame, metrics: pd.DataFrame | None, layers: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per pass/review plant with every scoring input; plant-file columns win over same-named layer columns."""
    df = plants[plants["filter_status"].isin(["pass", "review"])].drop(columns="geometry", errors="ignore").copy()
    if metrics is not None:
        df = df.merge(metrics[["eia_id", "metrics_status", "capture_rate_potential", "curtailment_pct", "sced_net_cf"]],
                      on="eia_id", how="left")
    for name, lay in layers.items():
        keep = ["eia_id", *[c for c in lay.columns if c not in df.columns]]
        df = df.merge(lay[keep], on="eia_id", how="left")
    return df.reset_index(drop=True)


# ---- scoring ----------------------------------------------------------------------------------------------------------
def score(df: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    s, a = cfg["scoring"], cfg["assumptions"]
    miss = s["missing_share"]
    out = pd.DataFrame({"eia_id": df["eia_id"]})
    maxes: dict[str, float] = {}
    missing: dict[str, pd.Series] = {}
    notes: dict = {}

    def put(comp: str, pts: pd.Series, max_pts: float, why_missing: str | None = None):
        """Store a component; NaN → neutral and flagged missing."""
        pts = pd.Series(pts, index=df.index, dtype=float)
        isna = pts.isna()
        out[f"pts_{comp}"] = pts.where(~isna, max_pts * miss)
        maxes[comp] = max_pts
        missing[comp] = isna
        if why_missing:
            notes.setdefault("not_built", []).append(comp)

    # A — acquisition discount -----------------------------------------------------------------------------------------
    A = s["A_acquisition_discount"]
    ok = df.get("metrics_status", pd.Series(index=df.index, dtype=object)).eq("ok")
    for comp, key, hi in (("capture", "capture_rate", False), ("curtailment", "curtailment_pct", True)):
        spec = A[key]
        vals = df[spec["metric"]].where(ok)
        if s["breakpoint_mode"] == "quantile":
            fleet = vals.dropna()
            pts = quantile_points(vals, fleet, spec["quantile"], hi)
            notes[f"{comp}_quantiles"] = {k: round(float(fleet.quantile(int(k[1:]) / 100)), 4)
                                          for k in spec["quantile"] if k.startswith("q")}
        else:
            pts = vals.map(lambda v: band(v, spec["fixed"]))
        put(comp, pts, spec["max"])

    cf = df["sced_net_cf"].where(ok).fillna(df["net_ac_cf"])
    out["cf_used"] = cf
    out["cf_source"] = np.where(df["sced_net_cf"].where(ok).notna(), "sced", np.where(df["net_ac_cf"].notna(), "eia923", None))
    regions = cfg["benchmarks"]["regions"]
    out["region"] = [region_of(la, lo, regions) for la, lo in zip(df["lat"], df["lon"])]
    bench = {}
    for r in sorted(set(out["region"])):
        fixed = (cfg["benchmarks"].get("regional_cf") or {}).get(r)
        bench[r] = float(fixed) if fixed is not None else float(cf[out["region"] == r].median())
    notes["regional_cf_benchmark"] = {k: round(v, 4) for k, v in bench.items()}
    out["cf_benchmark"] = out["region"].map(bench)
    below_pts = (out["cf_benchmark"] - cf) * 100          # CF percentage points below the benchmark
    out["cf_below_benchmark_pts"] = below_pts
    cfb = A["cf_vs_benchmark"]["fixed"]
    put("cf_benchmark", below_pts.map(lambda v: np.nan if pd.isna(v) else
                                      next((float(b["pts"]) for b in cfb if "below_pts_gt" in b and v > b["below_pts_gt"]),
                                           float(cfb[-1]["else"]))), A["cf_vs_benchmark"]["max"])
    off = A["offtake_status"]
    put("offtake", pd.Series(float(off["unknown"]), index=df.index), off["max"])
    missing["offtake"] = pd.Series(True, index=df.index)   # scored at the 'unknown' value, but no data behind it
    out["offtake_confidence"] = "unknown"

    # B — repowering upside (vintage proxy) ----------------------------------------------------------------------------
    B = s["B_repowering_upside"]
    vp = B["vintage_proxy"]
    year = pd.to_datetime(df["cod_first"]).dt.year
    vintage = np.where(year <= vp["poly_monofacial_until"], 1.0, np.where(year <= vp["mixed_until"], 0.5, 0.0))
    vintage = pd.Series(vintage, index=df.index).where(year.notna())
    tech = df["module_tech"].fillna("")
    csi = tech.eq("c-Si")
    thin = tech.str.startswith("thin_film")
    put("poly", (B["polycrystalline"] * vintage).where(csi, 0.0).where(tech.ne(""), np.nan), B["polycrystalline"])
    # CdTe/CIGS of these vintages are monofacial; c-Si by vintage (bifacial_share overrides when EIA reports it)
    mono = (B["monofacial"] * vintage).where(~thin, float(B["monofacial"]))
    bif = df.get("bifacial_share")
    if bif is not None:
        mono = mono.where(bif.isna(), B["monofacial"] * (1 - bif))
    put("monofacial", mono.where(tech.ne(""), np.nan), B["monofacial"])
    trk = df["tracking_type"]
    tracker = pd.Series(np.select([trk.eq("fixed"), trk.isin(["single_axis", "dual_axis"]), trk.eq("mixed")],
                                  [1.0, vintage.fillna(np.nan), (1.0 + vintage.fillna(0)) / 2], default=np.nan),
                        index=df.index) * B["vintage_tracker_or_fixed"]
    put("tracker", tracker, B["vintage_tracker_or_fixed"])

    # C — physical envelope (host parcels) -----------------------------------------------------------------------------
    C = s["C_physical_envelope"]
    parcel_ok = df.get("parcel_status", pd.Series(index=df.index, dtype=object)).eq("ok") & \
        df.get("parcels_confidence", pd.Series(index=df.index, dtype=object)).ne("low")
    put("acres", df["acres_per_mw_parcel"].where(parcel_ok).map(lambda v: band(v, C["acres_per_mw_ac"]["bands"])),
        C["acres_per_mw_ac"]["max"])
    put("headroom", df.get("headroom_pct_unified", pd.Series(np.nan, index=df.index)).where(parcel_ok)
        .map(lambda v: band(v, C["expansion_headroom"]["bands"])), C["expansion_headroom"]["max"])
    ulc = df.get("unified_land_control", pd.Series(index=df.index, dtype=object)).where(parcel_ok)
    put("land_control", ulc.map(lambda v: np.nan if v is None or pd.isna(v) else (C["unified_land_control"] if bool(v) else 0.0)),
        C["unified_land_control"])

    # D — electrical ---------------------------------------------------------------------------------------------------
    D = s["D_electrical"]
    put("poi_kv", df["grid_voltage_kv"].map(lambda v: band(v, D["poi_voltage_kv"]["bands"])), D["poi_voltage_kv"]["max"])
    has_tx = df.get("transmission_source", pd.Series(index=df.index, dtype=object)).notna()
    # a null distance with the layer present = no 345 kV substation within the search radius: measured, scores the else band
    dist = df.get("dist_345kv_sub_mi", pd.Series(np.nan, index=df.index))
    far = D["distance_to_345kv_sub_mi"]["bands"][-1].get("else", 0)
    put("dist_345", dist.map(lambda v: band(v, D["distance_to_345kv_sub_mi"]["bands"])).where(dist.notna(), far)
        .where(has_tx, np.nan), D["distance_to_345kv_sub_mi"]["max"])
    put("load_pocket", pd.Series(np.nan, index=df.index), D["load_pocket_proximity"]["max"], "not_built")

    # E — thermal & cooling: no layer built yet -------------------------------------------------------------------------
    E = s["E_thermal_cooling"]
    for comp, key in (("lambda", "soil_lambda_w_mk"), ("drill", "drillability"), ("hours25", "hours_below_25c_drybulb"),
                      ("water", "depth_to_water_ft")):
        put(comp, pd.Series(np.nan, index=df.index), E[key]["max"], "not_built")
    out["thermal_response_test_required"] = None          # set once the SSURGO λ layer exists (λ < 1.0 W/m·K)

    # F — fixed-cost drag (penalty; not part of completeness) ----------------------------------------------------------
    fixed = float(sum(a["fixed_cost_defaults_usd"].values()))
    fiber_mi = df.get("fiber_lateral_miles")
    fiber_cost = (fiber_mi * a["fiber_lateral_cost_per_mile_usd"]).fillna(0) if fiber_mi is not None else 0.0
    out["fixed_cost_est_usd"] = fixed + fiber_cost
    out["fixed_cost_includes_fiber"] = fiber_mi is not None
    out["fixed_cost_per_kw_it"] = out["fixed_cost_est_usd"] / (df["firm_it_mw"] * 1000)
    out["pts_fixed_cost"] = out["fixed_cost_per_kw_it"].map(lambda v: band(v, s["F_fixed_cost_drag"]["bands"])).fillna(0.0)

    # sections, total, completeness ------------------------------------------------------------------------------------
    for sec, comps in SECTIONS.items():
        out[f"score_{sec}"] = out[[f"pts_{c}" for c in comps]].sum(axis=1)
    out["score_total"] = out[[f"score_{k}" for k in SECTIONS]].sum(axis=1)
    pos = [c for c in maxes]                               # A–E components (F is a penalty, not in maxes)
    total_max = sum(maxes[c] for c in pos)
    real = sum(maxes[c] * (~missing[c]).astype(float) for c in pos)
    out["data_completeness"] = (real / total_max).round(3)
    out["missing_inputs"] = [";".join(c for c in pos if missing[c].iloc[i]) or None for i in range(len(df))]
    notes["max_points"] = {c: maxes[c] for c in pos}
    notes["positive_max_total"] = total_max

    # display-only flags and identity ----------------------------------------------------------------------------------
    for col in ("plant_name", "county", "tier", "ac_mw", "filter_status", "flood_flag", "flood_status",
                "distribution_class_poi", "sb6_review_required"):
        out[col] = df[col] if col in df.columns else None

    ranked = out["filter_status"].isin(s["rank_statuses"])
    out["rank_overall"] = out["score_total"].where(ranked).rank(ascending=False, method="min").astype("Int64")
    out["rank_in_tier"] = out["score_total"].where(ranked).groupby(out["tier"]).rank(ascending=False, method="min").astype("Int64")
    return out, notes


# ---- report -----------------------------------------------------------------------------------------------------------
def report(scores: pd.DataFrame, notes: dict, path: Path) -> Path:
    cols = ["rank_overall", "rank_in_tier", "plant_name", "county", "tier", "ac_mw", "score_total",
            *[f"score_{k}" for k in SECTIONS], "data_completeness", "flood_flag"]
    ranked = scores[scores["rank_overall"].notna()].sort_values("rank_overall")
    fmt = lambda d: d[cols].to_markdown(index=False, floatfmt=".1f")
    lines = [f"# Phase 5 scores — {dt.date.today().isoformat()}", "",
             f"Ranked (pass): {len(ranked)}; scored, unranked (review): {int(scores['rank_overall'].isna().sum())}.",
             f"Score total median {ranked['score_total'].median():.1f}, range {ranked['score_total'].min():.1f}–{ranked['score_total'].max():.1f};"
             f" data completeness median {ranked['data_completeness'].median():.0%}.", "",
             "Breakpoints used:", "", f"- capture_rate_potential quantiles: {notes.get('capture_quantiles')}",
             f"- curtailment_pct quantiles: {notes.get('curtailment_quantiles')}",
             f"- regional CF benchmarks: {notes.get('regional_cf_benchmark')}",
             f"- not built yet (scored neutral at {load_config()['scoring']['missing_share']:.0%} of max): {sorted(set(notes.get('not_built', [])))}",
             "", "## Top 25", "", fmt(ranked.head(25)), "",
             "## Pecos (validation county)", "", fmt(scores[scores["county"].str.contains("Pecos", na=False)].sort_values("score_total", ascending=False)), "",
             "## Rank within tier", ""]
    for t in ("T1b", "T1a", "T2", "T3"):
        lines += [f"### {t}", "", fmt(ranked[ranked["tier"] == t].sort_values("rank_in_tier")), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run() -> Path:
    cfg = load_config()
    plants = pd.read_parquet(DATA_DIR / "plants.parquet")
    mp = DATA_DIR / "plant_metrics.parquet"
    metrics = pd.read_parquet(mp) if mp.exists() else None
    layers = {n: pd.read_parquet(DATA_DIR / "layers" / f"{n}.parquet") for n in LAYERS
              if (DATA_DIR / "layers" / f"{n}.parquet").exists()}
    log.info("scoring with metrics=%s, layers=%s", metrics is not None, sorted(layers))
    scores, notes = score(assemble(plants, metrics, layers), cfg)
    scores["score_version"] = str(cfg.get("version"))
    path = DATA_DIR / "scores.parquet"
    scores.to_parquet(path, index=False)
    rp = report(scores, notes, DATA_DIR / "validation" / "score_report.md")
    log.info("scores: %d plants → %s; report → %s", len(scores), path, rp)
    return path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    run()
