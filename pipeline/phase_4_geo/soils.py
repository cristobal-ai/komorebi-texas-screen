"""Layer 8: soils. Soil thermal conductivity (lambda) estimated from SSURGO texture, density and moisture.

Source: USDA NRCS Soil Data Access (SDA) REST, sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest (T-SQL, no key):
  1. map units intersecting the array (mupolygon, clipped areas as weights);
  2. components (comppct_r) and horizons (sand, clay, bulk density at 1/3 bar, water at 1/3 bar = field capacity);
  3. restrictive layers (corestrictions: petrocalcic = caliche hardpan, lithic / paralithic bedrock, ...) and muaggatt.
Cached per plant as data/raw/soils/plants/<eia_id>.json (delete to refresh).

Lambda per horizon, Cote & Konrad (2005), at field capacity (the scored "moist" value):
    n      = 1 - rho_b / rho_s                       porosity (rho_s = particle density, 2.65 g/cm3)
    lam_s  = lam_q^q * lam_o^(1-q)                   solids; quartz share q ~ sand fraction (Johansen: lam_o 2.0 if q > 0.2 else 3.0)
    lam_sat= lam_s^(1-n) * lam_w^n                   saturated
    lam_dry= chi * 10^(-eta * n)                     dry (natural mineral soil: chi 0.75, eta 1.2)
    Sr     = theta_fc / n;  Ke = kappa*Sr / (1 + (kappa-1)*Sr)   (kappa 3.55 sands, 1.9 silty/clayey)
    lam    = (lam_sat - lam_dry) * Ke + lam_dry
Horizons are depth-weighted over 0-`depth_cm`, components by comppct_r (components without horizon data, e.g. rock
outcrop, are left out and lower soil_data_share), map units by clipped area.

SSURGO describes the top ~2 m only; a 150 m loop bore runs mostly through rock. This is a near-surface screen:
soil_confidence is never better than medium, and a plant with lambda < 1.0 is flagged for a thermal response test.

Output data/layers/soils.parquet, one row per eia_id:
    soil_lambda_w_mk (scored), soil_lambda_dry_w_mk, soil_lambda_sat_w_mk
    soil_sand_pct, soil_clay_pct, soil_bulk_density, soil_theta_fc     depth/component/area weighted
    dominant_soil (component name and share of the array), dominant_mapunit, n_mapunits, soil_data_share
    restriction_kinds, restriction_min_depth_cm, restriction_share (area share with a restriction within depth_cm),
    bedrock_depth_cm_min, soil_status (ok | no_soil_data), soil_source, soil_fetched, soil_confidence
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely import make_valid

from pipeline.common import load_config, raw_dir
from pipeline.phase_4_geo import common as g

log = logging.getLogger(__name__)
NAME = "soils"
HZ_COLS = ["mukey", "muname", "cokey", "compname", "comppct_r", "hzdept_r", "hzdepb_r", "sandtotal_r", "claytotal_r",
           "dbthirdbar_r", "wthirdbar_r"]


# ---- source -----------------------------------------------------------------------------------------------------------
def sda(query: str, cfg: dict, session=requests, sleep=time.sleep) -> pd.DataFrame:
    """Run one SDA query; the JSON+COLUMNNAME format puts the column names in the first row."""
    c = cfg["layers"][NAME]
    err = ""
    for attempt in range(1, c["retries"] + 1):
        try:
            r = session.post(c["sda_url"], data={"query": query, "format": "JSON+COLUMNNAME"}, timeout=(20, 180))
            if r.ok:
                rows = (r.json() if r.text.strip() else {}).get("Table", [])
                return pd.DataFrame(rows[1:], columns=rows[0]) if rows else pd.DataFrame()
            err = f"HTTP {r.status_code} {r.text[:300]}"
            if r.status_code == 400:                      # SQL / geometry error: retrying will not help
                raise RuntimeError(f"SDA: {err}")
        except requests.RequestException as e:
            err = type(e).__name__
        sleep(min(60, 5 * 2 ** (attempt - 1)))
        log.warning("SDA retry %d: %s", attempt, err)
    raise RuntimeError(f"SDA failed after {c['retries']} attempts: {err}")


def footprint_wkt(geom, tol: float) -> str:
    """Valid, simplified WGS84 WKT for SDA (a ~30 m tolerance keeps big arrays to a few thousand characters)."""
    gm = make_valid(geom).buffer(0)
    s = gm.simplify(tol, preserve_topology=True)
    return (s if not s.is_empty else gm).wkt


def fetch_plant(eia_id: int, geom_4326, cfg: dict, session=requests, sleep=time.sleep) -> dict:
    c = cfg["layers"][NAME]
    d = raw_dir(NAME) / "plants"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{eia_id}.json"
    if p.exists():
        return json.loads(p.read_text())
    wkt = footprint_wkt(geom_4326, c["simplify_deg"])
    g_ = f"geometry::STGeomFromText('{wkt}', 4326)"
    mu = sda(f"SELECT P.mukey, SUM(P.mupolygongeo.STIntersection({g_}).STArea()) AS a FROM mupolygon AS P "
             f"WHERE P.mupolygongeo.STIntersects({g_}) = 1 GROUP BY P.mukey", cfg, session, sleep)
    out = {"mapunits": mu.to_dict("records"), "horizons": [], "restrictions": [], "aggregates": [],
           "fetched": dt.date.today().isoformat()}
    if len(mu):
        keys = ",".join(str(int(k)) for k in mu["mukey"])
        out["horizons"] = sda(
            f"SELECT mu.mukey, mu.muname, c.cokey, c.compname, c.comppct_r, h.hzdept_r, h.hzdepb_r, h.sandtotal_r, "
            f"h.claytotal_r, h.dbthirdbar_r, h.wthirdbar_r FROM mapunit mu JOIN component c ON c.mukey = mu.mukey "
            f"LEFT JOIN chorizon h ON h.cokey = c.cokey WHERE mu.mukey IN ({keys})", cfg, session, sleep).to_dict("records")
        out["restrictions"] = sda(
            f"SELECT c.mukey, c.cokey, c.comppct_r, r.reskind, r.resdept_r FROM component c "
            f"JOIN corestrictions r ON r.cokey = c.cokey WHERE c.mukey IN ({keys})", cfg, session, sleep).to_dict("records")
        out["aggregates"] = sda(f"SELECT mukey, brockdepmin FROM muaggatt WHERE mukey IN ({keys})",
                                cfg, session, sleep).to_dict("records")
    p.write_text(json.dumps(out))
    sleep(c["pause_s"])
    return out


# ---- lambda -----------------------------------------------------------------------------------------------------------
def lambda_cote_konrad(sand_pct, clay_pct, bulk_density, theta_fc_pct, m: dict) -> dict:
    """Thermal conductivity (W/m-K) of one horizon: moist (field capacity), dry and saturated. Arrays welcome."""
    q = np.clip(np.asarray(sand_pct, float) / 100.0, 0, 1)
    n = np.clip(1.0 - np.asarray(bulk_density, float) / m["particle_density"], 0.05, 0.95)
    lam_o = np.where(q > 0.2, m["lambda_other_minerals"], m["lambda_other_minerals_low_quartz"])
    lam_s = m["lambda_quartz"] ** q * lam_o ** (1 - q)
    lam_sat = lam_s ** (1 - n) * m["lambda_water"] ** n
    lam_dry = m["chi"] * 10 ** (-m["eta"] * n)
    sr = np.clip(np.asarray(theta_fc_pct, float) / 100.0 / n, 0, 1)
    kappa = np.where(q * 100 >= m["sand_kappa_min_pct"], m["kappa_sand"], m["kappa_fine"])
    ke = kappa * sr / (1 + (kappa - 1) * sr)
    return {"moist": (lam_sat - lam_dry) * ke + lam_dry, "dry": lam_dry, "sat": lam_sat}


def depth_weighted(h: pd.DataFrame, depth_cm: float) -> pd.DataFrame:
    """Per component (cokey): horizon values weighted by thickness inside 0-depth_cm; components with no data dropped."""
    h = h.copy()
    for col in ("hzdept_r", "hzdepb_r", "sandtotal_r", "claytotal_r", "dbthirdbar_r", "wthirdbar_r", "comppct_r"):
        h[col] = pd.to_numeric(h[col], errors="coerce")
    h = h.dropna(subset=["hzdept_r", "hzdepb_r", "sandtotal_r", "dbthirdbar_r", "wthirdbar_r"])
    h["w"] = (h["hzdepb_r"].clip(upper=depth_cm) - h["hzdept_r"].clip(upper=depth_cm)).clip(lower=0)
    h = h[h["w"] > 0]
    if h.empty:
        return pd.DataFrame(columns=["mukey", "cokey", "compname", "muname", "comppct_r"])
    vals = ["lam", "lam_dry", "lam_sat", "sandtotal_r", "claytotal_r", "dbthirdbar_r", "wthirdbar_r"]
    agg = h.groupby(["mukey", "cokey", "compname", "muname", "comppct_r"]).apply(
        lambda x: pd.Series({v: np.average(x[v], weights=x["w"]) for v in vals}), include_groups=False)
    return agg.reset_index()


def summarize(raw: dict, cfg: dict) -> dict:
    c = cfg["layers"][NAME]
    mu = pd.DataFrame(raw.get("mapunits") or [])
    empty = {"soil_status": "no_soil_data", "n_mapunits": int(len(mu)), "soil_data_share": 0.0}
    if mu.empty or not raw.get("horizons"):
        return empty
    mu["a"] = pd.to_numeric(mu["a"], errors="coerce").fillna(0)
    mu["share"] = mu["a"] / mu["a"].sum() if mu["a"].sum() > 0 else 1 / len(mu)
    h = pd.DataFrame(raw["horizons"])
    lam = lambda_cote_konrad(pd.to_numeric(h["sandtotal_r"], errors="coerce"), pd.to_numeric(h["claytotal_r"], errors="coerce"),
                             pd.to_numeric(h["dbthirdbar_r"], errors="coerce"), pd.to_numeric(h["wthirdbar_r"], errors="coerce"),
                             c["model"])
    h["lam"], h["lam_dry"], h["lam_sat"] = lam["moist"], lam["dry"], lam["sat"]
    comp = depth_weighted(h, c["depth_cm"])
    if comp.empty:
        return empty
    # weight of a component on the array = map-unit area share x comppct / 100
    comp = comp.astype({"mukey": str}).merge(mu[["mukey", "share"]].astype({"mukey": str}), on="mukey")
    comp["w"] = comp["share"] * comp["comppct_r"] / 100.0
    covered = float(comp["w"].sum())
    if covered <= 0:
        return empty
    wavg = lambda col: float(np.average(comp[col], weights=comp["w"]))
    dom = comp.groupby("compname")["w"].sum().sort_values(ascending=False)
    dmu = mu.sort_values("share", ascending=False).iloc[0]
    row = {
        "soil_status": "ok", "n_mapunits": int(len(mu)), "soil_data_share": round(covered, 3),
        "soil_lambda_w_mk": round(wavg("lam"), 3), "soil_lambda_dry_w_mk": round(wavg("lam_dry"), 3),
        "soil_lambda_sat_w_mk": round(wavg("lam_sat"), 3), "soil_sand_pct": round(wavg("sandtotal_r"), 1),
        "soil_clay_pct": round(wavg("claytotal_r"), 1), "soil_bulk_density": round(wavg("dbthirdbar_r"), 2),
        "soil_theta_fc": round(wavg("wthirdbar_r"), 1),
        "dominant_soil": f"{dom.index[0]} ({dom.iloc[0]:.0%})",
        "dominant_mapunit": next((x["muname"] for x in raw["horizons"] if str(x["mukey"]) == str(dmu["mukey"])), None),
    }
    r = pd.DataFrame(raw.get("restrictions") or [])
    if len(r):
        r["resdept_r"] = pd.to_numeric(r["resdept_r"], errors="coerce")
        r["comppct_r"] = pd.to_numeric(r["comppct_r"], errors="coerce").fillna(0)
        r["mukey"] = r["mukey"].astype(str)
        r = r[r["resdept_r"].fillna(0) < c["depth_cm"]]
        r = r.merge(mu[["mukey", "share"]].astype({"mukey": str}), on="mukey")
    if len(r):
        per_comp = r.drop_duplicates("cokey")
        row["restriction_kinds"] = "; ".join(sorted({str(k) for k in r["reskind"].dropna()})) or None
        row["restriction_min_depth_cm"] = float(r["resdept_r"].min()) if r["resdept_r"].notna().any() else np.nan
        row["restriction_share"] = round(float((per_comp["share"] * per_comp["comppct_r"] / 100).sum()), 3)
    else:
        row |= {"restriction_kinds": None, "restriction_min_depth_cm": np.nan, "restriction_share": 0.0}
    a = pd.DataFrame(raw.get("aggregates") or [])
    b = pd.to_numeric(a["brockdepmin"], errors="coerce") if len(a) else pd.Series(dtype=float)
    row["bedrock_depth_cm_min"] = float(b.min()) if b.notna().any() else np.nan
    return row


# ---- driver -----------------------------------------------------------------------------------------------------------
def run(session=requests) -> Path:
    cfg = load_config()
    c = cfg["layers"][NAME]
    plants = g.load_plants()
    geo = plants.to_crs(g.METRIC_CRS).geometry
    fps = gpd.GeoSeries([gm.buffer(c["point_buffer_m"]) if pt else gm for gm, pt in zip(geo, plants["geometry_is_point"])],
                        crs=g.METRIC_CRS).to_crs("EPSG:4326")
    rows = []
    for i, (pid, fp) in enumerate(zip(plants["eia_id"].astype(int), fps), 1):
        raw = fetch_plant(pid, fp, cfg, session)
        rows.append({"eia_id": pid} | summarize(raw, cfg) | {"soil_fetched": raw.get("fetched")})
        if i % 10 == 0:
            log.info("soils: %d / %d plants", i, len(plants))
    df = pd.DataFrame(rows)
    for col in ("soil_lambda_w_mk", "soil_lambda_dry_w_mk", "soil_lambda_sat_w_mk", "soil_sand_pct", "soil_clay_pct",
                "soil_bulk_density", "soil_theta_fc", "restriction_min_depth_cm", "restriction_share", "bedrock_depth_cm_min"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    for col in ("dominant_soil", "dominant_mapunit", "restriction_kinds"):
        if col not in df.columns:
            df[col] = None
    df["soil_source"] = "usda nrcs ssurgo via soil data access; lambda: cote & konrad (2005) at field capacity"
    ok = df["soil_status"].eq("ok") & (df["soil_data_share"] >= c["min_data_share_medium"]) & ~plants["geometry_is_point"].to_numpy()
    df["soil_confidence"] = np.where(ok, "medium", "low")
    log.info("soils: %d plants, %d with soil data; lambda (moist) median %.2f W/m-K (range %.2f-%.2f); %d with a "
             "restrictive layer within %d cm", len(df), int(df["soil_status"].eq("ok").sum()), df["soil_lambda_w_mk"].median(),
             df["soil_lambda_w_mk"].min(), df["soil_lambda_w_mk"].max(), int((df["restriction_share"] > 0).sum()), c["depth_cm"])
    return g.write_layer(NAME, df)
