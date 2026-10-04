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
import time
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
def _gdb_roots(names: list[str]) -> list[str]:
    """File-geodatabase folders inside a zip, at any depth: 'a/b/x.gdb/file' → 'a/b/x.gdb'."""
    roots = set()
    for n in names:
        parts = n.split("/")
        for i, part in enumerate(parts[:-1] if not n.endswith("/") else parts):
            if part.lower().endswith(".gdb"):
                roots.add("/".join(parts[: i + 1]))
                break
    return sorted(roots)


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
            gdbs = _gdb_roots(names)
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


def _read_source(path: str, layer: str | None, cfg: dict, bbox_5070=None, mask_5070=None) -> gpd.GeoDataFrame | None:
    """One source filtered by a bounding box or a (multi)polygon mask given in EPSG:5070; columns renamed to owner / prop_id /
    land_use / market_value / land_value / county / year (missing ones None), result in EPSG:5070. None if the source does not
    cover the area."""
    import pyogrio

    fmap = cfg["layers"]["parcels"]["fields"]
    info = pyogrio.read_info(path, layer=layer)
    crs = info.get("crs")
    if not crs:
        return None
    area = box(*bbox_5070) if bbox_5070 is not None else mask_5070
    src_geom = gpd.GeoSeries([area], crs="EPSG:5070").to_crs(crs).iloc[0]
    sb, tb = src_geom.bounds, info["total_bounds"]
    name = Path(path.split("/vsizip/")[-1]).name
    log.info("%s: source crs %s bounds %s; request bounds %s", name, crs, None if tb is None else [round(float(x)) for x in tb],
             [round(float(x)) for x in sb])
    if tb is None or sb[2] < tb[0] or sb[0] > tb[2] or sb[3] < tb[1] or sb[1] > tb[3]:
        log.warning("%s: request lies outside the source bounds (wrong CRS in the plant geometry?)", name)
        return None
    fields = [str(f) for f in info["fields"]]
    cols = {attr: _pick(fields, cands) for attr, cands in fmap.items()}
    if cols["owner"] is None:
        log.warning("%s: no owner column; fields are %s", Path(path).name, fields)
    use = sorted({c for c in cols.values() if c})
    kw = {"bbox": tuple(sb)} if bbox_5070 is not None else {"mask": src_geom}
    t0 = time.time()
    df = pyogrio.read_dataframe(path, layer=layer, columns=use, **kw)
    log.info("%s: %s read returned %d rows in %.1f s", name, "bbox" if bbox_5070 is not None else "mask", len(df), time.time() - t0)
    if df.empty:
        return None
    df = df.to_crs("EPSG:5070")
    out = gpd.GeoDataFrame({a: (df[c].to_numpy() if c else None) for a, c in cols.items()},
                           geometry=df.geometry.to_numpy(), crs="EPSG:5070")
    out["source_file"] = Path(path.split("/vsizip/")[-1]).name
    return out


def _combine(frames: list, cfg: dict) -> gpd.GeoDataFrame:
    frames = [f for f in frames if f is not None]
    if not frames:
        fmap = cfg["layers"]["parcels"]["fields"]
        return gpd.GeoDataFrame({a: [] for a in [*fmap, "source_file"]}, geometry=[], crs="EPSG:5070")
    return pd.concat(frames, ignore_index=True)


def read_neighborhood(sources, bbox_5070, cfg: dict) -> gpd.GeoDataFrame:
    """Parcels of every source intersecting one box (EPSG:5070). Empty frame if no source covers it."""
    return _combine([_read_source(p, l, cfg, bbox_5070=bbox_5070) for p, l in sources], cfg)


def read_mask(sources, mask_5070, cfg: dict) -> gpd.GeoDataFrame:
    """Parcels of every source intersecting a (multi)polygon, in ONE pass per source: for a statewide file this replaces one
    full scan per plant."""
    out = _combine([_read_source(p, l, cfg, mask_5070=mask_5070) for p, l in sources], cfg)
    if out.empty and hasattr(mask_5070, "geoms"):
        log.warning("mask read returned nothing; retrying box by box (%d boxes)", len(mask_5070.geoms))
        out = _combine([_read_source(p, l, cfg, bbox_5070=b.bounds)
                        for b in mask_5070.geoms for p, l in sources], cfg)
        out = out.drop_duplicates(subset=["prop_id", "owner"]) if not out.empty and out["prop_id"].notna().any() else out
    return out


def inspect(sources) -> str:
    """What is in the parcel files: layer, CRS, feature count, bounds and fields (no features are read)."""
    import pyogrio

    lines = []
    for path, layer in sources:
        info = pyogrio.read_info(path, layer=layer)
        crs = info.get("crs")
        lines += [f"{Path(path.split('/vsizip/')[-1]).name}  layer={layer}", f"  features: {info.get('features')}  crs: {crs}  "
                  f"geometry: {info.get('geometry_type')}", f"  bounds: {info.get('total_bounds')}",
                  f"  fields: {[str(f) for f in info['fields']]}"]
        if path.startswith("/vsizip/") and Path(path.split('/vsizip/')[-1].split(".zip")[0] + ".zip").exists():
            lines.append("  note: read straight from a zip; extract it first for much faster reads on large files")
    return "\n".join(lines) if lines else "no parcel datasets found"


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
    P = plants.to_crs("EPSG:5070")
    arrays = {}
    for (_, pl), geom in zip(plants.iterrows(), P.geometry):
        arrays[int(pl["eia_id"])] = geom.buffer(c["point_buffer_m"]) if bool(pl["geometry_is_point"]) else geom

    need = [pid for pid in arrays if not (cache / f"{pid}.parquet").exists()]
    if need:
        sources = discover_sources(folder)
        log.info("parcel sources in %s: %d dataset(s); %d plant(s) need a parcel read", folder, len(sources), len(need))
        if sources:
            mask = unary_union([box(*arrays[pid].buffer(c["neighborhood_m"]).bounds) for pid in need])
            log.info("reading parcels around %d plants in one pass per source (statewide files take several minutes)", len(need))
            allp = read_mask(sources, mask, cfg)
            log.info("read %d parcels", len(allp))
            if not allp.empty:
                tree = allp.sindex
                for pid in need:
                    area = arrays[pid].buffer(c["neighborhood_m"])
                    sel = allp.iloc[np.sort(tree.query(area, predicate="intersects"))]
                    if not sel.empty:                   # never cache 'no coverage': the county file may be added later
                        sel.to_parquet(cache / f"{pid}.parquet")

    rows = []
    for (_, pl) in plants.iterrows():
        pid = int(pl["eia_id"])
        cp = cache / f"{pid}.parquet"
        parcels = gpd.read_parquet(cp) if cp.exists() else None
        r = analyze(arrays[pid], parcels, cfg, float(pl["ac_mw"]), bool(pl["geometry_is_point"]))
        r["eia_id"] = pid
        rows.append(r)
    df = pd.DataFrame(rows)
    df = df[["eia_id"] + [x for x in df.columns if x != "eia_id"]]
    log.info("parcels: %s", df["parcel_status"].value_counts().to_dict())
    if (df["parcel_status"] == "no_parcel_data").any():
        miss = plants.loc[plants["eia_id"].isin(df.loc[df["parcel_status"] == "no_parcel_data", "eia_id"]), "county"]
        log.warning("no parcel file covers %d plants; counties to download: %s", len(miss), sorted(set(miss.dropna())))
    return g.write_layer(NAME, df)


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description="parcel files: --inspect lists layers, CRS, fields; --probe EIA_ID reads one plant")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--probe", type=int, default=None, help="eia_id: read that plant's 3 km neighbourhood by box and by mask")
    a = ap.parse_args()
    folder = raw_dir(NAME) / "manual"
    if a.probe is not None:
        cfg = load_config()
        pl = g.load_plants()
        pl = pl[pl["eia_id"] == a.probe].to_crs("EPSG:5070")
        if pl.empty:
            raise SystemExit(f"eia_id {a.probe} not in plants.parquet")
        print("plant geometry bounds (EPSG:5070):", [round(x) for x in pl.geometry.iloc[0].bounds])
        area = box(*pl.geometry.iloc[0].buffer(cfg["layers"]["parcels"]["neighborhood_m"]).bounds)
        srcs = discover_sources(folder)
        print("sources:", len(srcs))
        print("box rows :", len(read_neighborhood(srcs, area.bounds, cfg)))
        print("mask rows:", len(read_mask(srcs, area, cfg)))
        raise SystemExit(0)
    if not a.inspect:
        ap.error("give --inspect or --probe EIA_ID")
    out = inspect(discover_sources(folder))
    print(out if not out.startswith("no parcel") else
          f"no parcel datasets found in {folder}\nPut the extracted .gdb folder (or a GeoPackage / shapefile / zip) directly in that folder.")
