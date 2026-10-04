"""Layer 3: parcels (TxGIO StratMap Land Parcels). Site footprint, expansion headroom and land control per plant.

The USPVDB polygon is the *array*; the parcels it sits on are the *site*. Per plant:
    host parcels            parcels the array overlaps by >= min_overlap_acres or >= min_overlap_frac of the array
    parcel_acres_host       their total area (computed from geometry in EPSG:5070, not the GIS_AREA attribute)
    acres_per_mw_parcel     parcel_acres_host / AC MW — the Phase 4 replacement for array acres/MW in the footprint filter
    host_cover_share        array area / host-parcel area (low = a small leased corner of a big parcel: parcel area overstates the site)
    expansion headroom      (parcel acres − array acres) / array acres, on host parcels and on the unified holding
    unified holding         host parcels + same-owner parcels touching them (breadth-first, max_expansion_rounds, within neighborhood_m)
    unified_land_control    one owner holds >= unified_min_owner_share of the host acres
    owners / land use / appraised value of the host parcels
parcel_status: ok | no_parcel_data (no parcel file covers the plant: drop that county's file in data/raw/parcels/manual/)
               | no_parcel_overlap (parcels nearby but none under the array)
Owners are matched after stripping punctuation and LLC/Inc/LP-style words; two LLCs of one sponsor are NOT merged.
Parcel data is an annual snapshot from county appraisal districts: `parcel_vintage` says which.

    python -m pipeline.run --phase 4 --layer parcels [--load]
"""
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box
from shapely.ops import unary_union

from pipeline.common import load_config, norm, raw_dir
from pipeline.phase_4_geo import common as g

log = logging.getLogger(__name__)
NAME = "parcels"
M2_PER_ACRE = 4046.8564224
VECTOR_SUFFIXES = (".shp", ".gpkg", ".geojson", ".json", ".parquet")


# ---- owners -----------------------------------------------------------------------------------------------------------
def norm_owner(name, stop: set[str]) -> str:
    """'ACME SOLAR, LLC' and 'Acme Solar L.L.C.' → 'acme solar'. Empty / missing → ''."""
    if name is None or (isinstance(name, float) and np.isnan(name)):
        return ""
    t = re.sub(r"\bL\.?L\.?C\.?\b", "llc", str(name), flags=re.I)
    words = re.sub(r"[^a-z0-9]+", " ", t.lower()).split()
    return " ".join(w for w in words if w not in stop)


# ---- reading ----------------------------------------------------------------------------------------------------------
def discover_sources(folder: Path) -> list[tuple[str, str | None]]:
    """(gdal path, layer) for every vector dataset in the folder: loose shapefiles/GeoPackages/GeoJSON/GeoParquet,
    File Geodatabases (*.gdb folders) and zips of any of those."""
    import pyogrio

    out: list[tuple[str, str | None]] = []

    def add_dataset(path: str):
        try:
            layers = pyogrio.list_layers(path)
        except Exception as e:
            log.warning("cannot list layers of %s (%s)", path, e)
            return
        for name, gtype in layers:
            if gtype and "Polygon" in str(gtype):
                out.append((path, str(name)))
                return                          # one polygon layer per dataset is enough
        log.warning("no polygon layer in %s: %s", path, [str(r[0]) for r in layers])

    if not folder.exists():
        return out
    for p in sorted(folder.rglob("*")):
        if p.is_dir() and p.suffix.lower() == ".gdb":
            add_dataset(str(p))
        elif p.is_file() and p.suffix.lower() in VECTOR_SUFFIXES and ".gdb" not in str(p.parent).lower():
            add_dataset(str(p))
        elif p.is_file() and p.suffix.lower() == ".zip":
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
            gdbs = sorted({n.split("/")[0] for n in names if n.split("/")[0].lower().endswith(".gdb")})
            members = [n for n in names if n.lower().endswith(VECTOR_SUFFIXES) and ".gdb/" not in n.lower()]
            for gdb in gdbs:
                add_dataset(f"/vsizip/{p}/{gdb}")
            for m in members:
                add_dataset(f"/vsizip/{p}/{m}")
    return out


def _pick(fields: list[str], candidates: list[str]) -> str | None:
    by = {norm(f): f for f in fields}
    for c in candidates:
        if norm(c) in by:
            return by[norm(c)]
    return None


def read_neighborhood(sources: list[tuple[str, str | None]], bbox_5070: tuple[float, float, float, float],
                      cfg: dict) -> gpd.GeoDataFrame:
    """Parcels of every source that intersect the box (EPSG:5070), columns renamed to owner / prop_id / land_use /
    market_value / land_value / county / year (missing ones are None), in EPSG:5070. Empty frame if no source covers it."""
    import pyogrio

    fmap = cfg["layers"]["parcels"]["fields"]
    frames = []
    for path, layer in sources:
        info = pyogrio.read_info(path, layer=layer)
        crs = info.get("crs")
        if not crs:
            continue
        src_box = gpd.GeoSeries([box(*bbox_5070)], crs="EPSG:5070").to_crs(crs).total_bounds
        tb = info["total_bounds"]
        if tb is None or src_box[2] < tb[0] or src_box[0] > tb[2] or src_box[3] < tb[1] or src_box[1] > tb[3]:
            continue
        fields = [str(f) for f in info["fields"]]
        cols = {attr: _pick(fields, cands) for attr, cands in fmap.items()}
        missing = [a for a, c in cols.items() if c is None and a in ("owner",)]
        if missing:
            log.warning("%s: no column for %s; fields are %s", Path(path).name, missing, fields)
        use = sorted({c for c in cols.values() if c})
        df = pyogrio.read_dataframe(path, layer=layer, bbox=tuple(src_box), columns=use)
        if df.empty:
            continue
        df = df.to_crs("EPSG:5070")
        out = gpd.GeoDataFrame({a: (df[c].to_numpy() if c else None) for a, c in cols.items()},
                               geometry=df.geometry.to_numpy(), crs="EPSG:5070")
        out["source_file"] = Path(path.split("/vsizip/")[-1]).name
        frames.append(out)
    if not frames:
        return gpd.GeoDataFrame({a: [] for a in [*fmap, "source_file"]}, geometry=[], crs="EPSG:5070")
    return pd.concat(frames, ignore_index=True)


# ---- analysis ---------------------------------------------------------------------------------------------------------
def analyze(array_geom, parcels: gpd.GeoDataFrame, cfg: dict, ac_mw: float, point_fallback: bool = False) -> dict:
    """Parcel metrics for one plant. array_geom in EPSG:5070 (metres); parcels in EPSG:5070 with the renamed columns."""
    c = cfg["layers"]["parcels"]
    stop = set(c["owner_stopwords"])
    array_m2 = float(array_geom.area)
    row = {"parcel_status": "no_parcel_data", "n_host_parcels": 0, "parcel_acres_host": np.nan, "parcel_acres_unified": np.nan,
           "adjacent_same_owner_acres": np.nan, "array_acres_calc": array_m2 / M2_PER_ACRE, "host_cover_share": np.nan,
           "headroom_pct_host": np.nan, "headroom_pct_unified": np.nan, "acres_per_mw_parcel": np.nan, "host_owners": None,
           "largest_owner_share": np.nan, "unified_land_control": None, "land_use_codes": None, "mkt_value_total": np.nan,
           "land_value_per_acre": np.nan, "parcel_vintage": None, "parcel_source": None, "parcels_confidence": None}
    if parcels is None or parcels.empty:
        return row
    parcels = parcels.reset_index(drop=True)
    valid = parcels.geometry.is_valid
    if not valid.all():
        parcels.loc[~valid, parcels.geometry.name] = parcels.geometry[~valid].buffer(0)
    tree = parcels.sindex
    idx = np.sort(tree.query(array_geom, predicate="intersects"))
    if len(idx) == 0:
        row["parcel_status"] = "no_parcel_overlap"
        return row
    inter = np.array([parcels.geometry.iloc[i].intersection(array_geom).area for i in idx])
    min_m2 = max(c["min_overlap_acres"] * M2_PER_ACRE, c["min_overlap_frac"] * array_m2)
    # small arrays (or point-fallback buffers) must still find their parcel: always keep the largest overlap
    keep = inter >= min_m2
    if not keep.any():
        keep = inter == inter.max()
    host = parcels.iloc[idx[keep]].copy()
    host = host.loc[~host.geometry.apply(lambda gg: gg.wkb).duplicated()]
    host_m2 = host.geometry.area
    host_acres = float(host_m2.sum() / M2_PER_ACRE)
    owner_n = host["owner"].map(lambda v: norm_owner(v, stop))
    by_owner = host_m2.groupby(owner_n.to_numpy()).sum().sort_values(ascending=False)
    shares = by_owner / by_owner.sum()
    named = shares.drop(index="", errors="ignore")

    # unified holding: same-owner parcels touching the host parcels, breadth-first
    main = set(named.index[:1]) | {o for o in named.index if named[o] >= 0.10}
    held = set(host.index)
    frontier = unary_union(list(host.geometry))
    added = []
    all_owner_n = parcels["owner"].map(lambda v: norm_owner(v, stop))
    for _ in range(c["max_expansion_rounds"]):
        if not main:
            break
        near = np.sort(tree.query(frontier.buffer(c["adjacency_tol_m"]), predicate="intersects"))
        new = [i for i in near if i not in held and all_owner_n.iloc[i] in main]
        if not new:
            break
        held.update(new)
        added.extend(new)
        frontier = unary_union([parcels.geometry.iloc[i] for i in new])
    added_acres = float(sum(parcels.geometry.iloc[i].area for i in added) / M2_PER_ACRE)
    unified_acres = host_acres + added_acres
    array_acres = array_m2 / M2_PER_ACRE

    mkt = pd.to_numeric(host["market_value"], errors="coerce")
    land = pd.to_numeric(host["land_value"], errors="coerce")
    top_use = host.assign(_m2=host_m2).groupby(host["land_use"].astype(str))["_m2"].sum().sort_values(ascending=False)
    years = pd.to_numeric(host["year"], errors="coerce").dropna()
    n_owners = len(named)
    cover = array_acres / host_acres if host_acres else np.nan
    row.update({
        "parcel_status": "ok", "n_host_parcels": int(len(host)), "parcel_acres_host": host_acres,
        "parcel_acres_unified": unified_acres, "adjacent_same_owner_acres": added_acres, "host_cover_share": cover,
        "headroom_pct_host": (host_acres - array_acres) / array_acres, "headroom_pct_unified": (unified_acres - array_acres) / array_acres,
        "acres_per_mw_parcel": host_acres / ac_mw if ac_mw else np.nan,
        "host_owners": "; ".join(f"{o.upper()} {s:.0%}" for o, s in named.head(3).items()) or None,
        "largest_owner_share": float(named.iloc[0]) if n_owners else np.nan,
        "unified_land_control": bool(n_owners and named.iloc[0] >= c["unified_min_owner_share"]),
        "land_use_codes": ";".join(top_use.head(3).index) or None,
        "mkt_value_total": float(mkt.sum()) if mkt.notna().any() else np.nan,
        "land_value_per_acre": float(land.sum() / host_acres) if land.notna().any() and host_acres else np.nan,
        "parcel_vintage": str(int(years.max())) if len(years) else None,
        "parcel_source": ";".join(sorted({str(x) for x in host["source_file"].dropna()})) or None,
        "parcels_confidence": "low" if point_fallback else ("medium" if (cover < 0.25 or len(host) > 12) else "high"),
    })
    return row


# ---- driver -----------------------------------------------------------------------------------------------------------
def run() -> Path:
    cfg = load_config()
    c = cfg["layers"]["parcels"]
    plants = g.load_plants()
    folder = raw_dir(NAME) / "manual"
    cache = raw_dir(NAME) / "selected"
    cache.mkdir(exist_ok=True)
    folder.mkdir(exist_ok=True)
    sources = None
    P = plants.to_crs("EPSG:5070")
    rows = []
    for (_, pl), geom in zip(plants.iterrows(), P.geometry):
        pid = int(pl["eia_id"])
        is_point = bool(pl["geometry_is_point"])
        array_geom = geom.buffer(c["point_buffer_m"]) if is_point else geom
        cp = cache / f"{pid}.parquet"
        if cp.exists():
            parcels = gpd.read_parquet(cp)
        else:
            if sources is None:
                sources = discover_sources(folder)
                log.info("parcel sources in %s: %d dataset(s)", folder, len(sources))
            if not sources:
                parcels = None
            else:
                bb = array_geom.buffer(c["neighborhood_m"]).bounds
                parcels = read_neighborhood(sources, bb, cfg)
                if not parcels.empty:                 # never cache 'no coverage': the county file may be added later
                    parcels.to_parquet(cp)
        r = analyze(array_geom, parcels, cfg, float(pl["ac_mw"]), is_point)
        r["eia_id"] = pid
        rows.append(r)
    df = pd.DataFrame(rows)
    cols = ["eia_id"] + [x for x in df.columns if x != "eia_id"]
    df = df[cols]
    counts = df["parcel_status"].value_counts().to_dict()
    log.info("parcels: %s", counts)
    if (df["parcel_status"] == "no_parcel_data").any():
        miss = plants.loc[plants["eia_id"].isin(df.loc[df["parcel_status"] == "no_parcel_data", "eia_id"]), "county"]
        log.warning("no parcel file covers %d plants; counties to download: %s", len(miss), sorted(set(miss.dropna())))
    return g.write_layer(NAME, df)
