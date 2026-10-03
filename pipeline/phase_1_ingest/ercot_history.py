"""Phase 3b ingest: SCED PV resource data and 15-minute real-time SPP, one operating day at a time.

    python -m pipeline.run --phase 3b                         # full window (api_history_floor → today − lag)
    python -m pipeline.run --phase 3b --since 2026-07-15 --until 2026-07-15    # one-day probe: check schema first

Each day is cached to Parquet and never re-fetched, so an interrupted run resumes where it stopped:
    data/raw/ercot/sced_15min/YYYY-MM-DD.parquet   all PVGR resources, per 15-min interval (metrics.sced_to_15min)
    data/raw/ercot/spp_15min/YYYY-MM-DD.parquet     hubs + load zones + crosswalk settlement points
    data/raw/ercot/{sced,spp}_15min/YYYY-MM-DD.none ERCOT returned no data for that day (not retried)

Needs ERCOT_API_USERNAME / _PASSWORD / _SUBSCRIPTION_KEY (pipeline/.env). Run it on a machine that can reach ercot.com.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import time
from pathlib import Path

import pandas as pd

from pipeline.common import find_col, load_config, raw_dir
from pipeline.phase_3_market import metrics

log = logging.getLogger(__name__)


def window(cfg: dict, since: dt.date | None = None, until: dt.date | None = None,
           today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    today = today or dt.date.today()
    mw = cfg["market_window"]
    floor = mw["api_history_floor"]
    floor = floor if isinstance(floor, dt.date) else dt.date.fromisoformat(str(floor))
    end = until or today - dt.timedelta(days=cfg["market_metrics"]["sced_lag_days"])
    start = since or max(floor, (pd.Timestamp(end) - pd.DateOffset(months=mw["months"])).date())
    return max(start, floor) if since is None else start, end


def _api():
    from gridstatus.ercot_api.ercot_api import ErcotAPI

    return ErcotAPI(
        username=os.environ.get("ERCOT_API_USERNAME"),
        password=os.environ.get("ERCOT_API_PASSWORD"),
        public_subscription_key=os.environ.get("ERCOT_API_SUBSCRIPTION_KEY"),
    )


def keep_locations(cfg: dict, xwalk: pd.DataFrame) -> set[str]:
    mm = cfg["market_metrics"]
    sps = {v.strip() for v in xwalk["ercot_settlement_point"].dropna().astype(str) if v.strip()}
    return sps | {mm["hub_reference"], *mm["price_locations_extra"]}


def fetch_sced_day(api, day: dt.date, cfg: dict) -> pd.DataFrame:
    data = api.get_60_day_sced_disclosure(date=pd.Timestamp(day), process=False)
    gen = data["sced_gen_resource"]
    return metrics.sced_to_15min(gen, cfg)


def fetch_spp_day(api, day: dt.date, keep: set[str] | None) -> pd.DataFrame:
    """keep=None returns every settlement point (used by find())."""
    df = api.get_spp_real_time_15_min(date=pd.Timestamp(day, tz=metrics.TZ), end=pd.Timestamp(day, tz=metrics.TZ) + pd.Timedelta(days=1))
    loc = find_col(df, "Location", required=True)
    price = find_col(df, "SPP", "Settlement Point Price", required=True)
    start = find_col(df, "Interval Start", required=True)
    zones = df[loc].astype(str).str.startswith(("HB_", "LZ_"))
    d = df if keep is None else df[zones | df[loc].isin(keep)]
    out = pd.DataFrame({
        "interval_start": pd.to_datetime(d[start], utc=True),
        "location": d[loc].astype(str).str.strip(),
        "spp": pd.to_numeric(d[price], errors="coerce"),
    })
    return out.dropna(subset=["spp"])


def _is_no_data(e: Exception) -> bool:
    """ERCOT answers 200 with no 'archives' key when nothing was posted for a day (a known archive gap); gridstatus
    raises KeyError('archives') for it. Retrying does not help, so it is treated like NoDataFoundException."""
    return type(e).__name__ == "NoDataFoundException" or (isinstance(e, KeyError) and e.args == ("archives",))


def _with_retries(fn, retries: int, pause: float):
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            if _is_no_data(e):
                raise
            if attempt == retries:
                raise
            wait = pause * 2 ** attempt * 5
            log.warning("attempt %d/%d failed (%s); retry in %.0fs", attempt, retries, e, wait)
            time.sleep(wait)


def reset_stale_spp(spp_dir: Path, keep: set[str]) -> int:
    """Price days only hold the locations asked for at fetch time. If the crosswalk now needs a node that was not
    kept (a new plant, a corrected settlement point), drop the cached price days so they are fetched again.
    SCED days are untouched. Returns the number of day files removed."""
    marker = spp_dir / "_keep.json"
    try:
        recorded = set(json.loads(marker.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        recorded = set()
    removed = 0
    if keep - recorded:
        for f in spp_dir.glob("*.parquet"):
            f.unlink()
            removed += 1
        if removed:
            log.warning("settlement points added since the price cache was built (%s): %d cached price days removed "
                        "and will be fetched again", sorted(keep - recorded)[:6], removed)
    marker.write_text(json.dumps(sorted(keep | recorded)), encoding="utf-8")
    return removed


def _cached(folder: Path, day: dt.date) -> bool:
    return (folder / f"{day}.parquet").exists() or (folder / f"{day}.none").exists()


def run(since: dt.date | None = None, until: dt.date | None = None, xwalk: pd.DataFrame | None = None,
        retry_missing: bool = False) -> dict:
    cfg = load_config()
    mm = cfg["market_metrics"]
    if xwalk is None:
        from pipeline.phase_3_market import crosswalk

        xwalk = pd.read_csv(crosswalk.CROSSWALK_CSV, encoding="utf-8-sig")
    start, end = window(cfg, since, until)
    keep = keep_locations(cfg, xwalk)
    sced_dir, spp_dir = raw_dir("ercot") / "sced_15min", raw_dir("ercot") / "spp_15min"
    sced_dir.mkdir(exist_ok=True), spp_dir.mkdir(exist_ok=True)
    reset_stale_spp(spp_dir, keep)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    if retry_missing:
        for folder in (sced_dir, spp_dir):
            for d in days:
                (folder / f"{d}.none").unlink(missing_ok=True)
    todo = [d for d in days if not (_cached(sced_dir, d) and _cached(spp_dir, d))]
    log.info("3b window %s → %s: %d days, %d already cached, %d to fetch", start, end, len(days), len(days) - len(todo), len(todo))
    failed: list[tuple[dt.date, str, str]] = []
    if todo:
        api = _api()
    for i, day in enumerate(todo, 1):
        for label, folder, fetch in (
            ("sced", sced_dir, lambda d=day: fetch_sced_day(api, d, cfg)),
            ("spp", spp_dir, lambda d=day: fetch_spp_day(api, d, keep)),
        ):
            if _cached(folder, day):
                continue
            try:
                df = _with_retries(fetch, mm["retries"], mm["throttle_seconds"])
            except Exception as e:
                if _is_no_data(e):
                    (folder / f"{day}.none").write_text("no data returned by ERCOT\n")
                    log.warning("%s %s: no data from ERCOT", label, day)
                else:
                    failed.append((day, label, str(e)[:200]))
                    log.error("%s %s failed: %s", label, day, e)
                continue
            if df.empty:
                log.warning("%s %s: empty after filtering", label, day)
            elif i == 1 or (since and since == until):
                log.info("%s %s: %d rows, columns %s, e.g. %s", label, day, len(df), list(df.columns), df.iloc[0].to_dict())
            df.to_parquet(folder / f"{day}.parquet", index=False)
            time.sleep(mm["throttle_seconds"])
        if i % 10 == 0:
            log.info("fetched %d/%d days (last %s)", i, len(todo), day)
    gaps = sorted({d for d in days for f in (sced_dir, spp_dir) if (f / f"{d}.none").exists()})
    if gaps:
        log.warning("%d days have no ERCOT data (archive gap; not retried — use --retry-missing): %s",
                    len(gaps), [g.isoformat() for g in gaps[:10]])
    if failed:
        log.warning("%d day-fetches failed; re-run to retry: %s", len(failed), failed[:10])
    return {"start": start, "end": end, "days": len(days), "fetched": len(todo), "failed": failed, "gaps": gaps}


def load_cached(start: dt.date, end: dt.date) -> tuple[pd.DataFrame, pd.DataFrame]:
    def read(folder: Path) -> pd.DataFrame:
        files = [f for f in sorted(folder.glob("*.parquet")) if start.isoformat() <= f.stem <= end.isoformat()]
        return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True) if files else pd.DataFrame()

    base = raw_dir("ercot")
    return read(base / "sced_15min"), read(base / "spp_15min")


def find(patterns: list[str], day: dt.date) -> None:
    """Print SCED resources (with Resource Type) and SPP settlement points whose name contains any pattern.

    For crosswalk gaps: a unit missing from the PVGR list (is it registered under another type?) and a resource
    node for a unit the Resource Node ↔ Unit mapping does not know. Fetches one day, ~30 s.
    """
    api = _api()
    pats = [p.upper() for p in patterns]
    gen = api.get_60_day_sced_disclosure(date=pd.Timestamp(day), process=False)["sced_gen_resource"]
    name, rtype = find_col(gen, "Resource Name", required=True), find_col(gen, "Resource Type", required=True)
    hsl = find_col(gen, "HSL", required=True)
    gen = gen.assign(_hsl=pd.to_numeric(gen[hsl], errors="coerce"))
    hit = gen[gen[name].astype(str).str.upper().map(lambda v: any(p in v for p in pats))]
    print(f"\nSCED resources matching {patterns} on {day}:")
    print(hit.groupby([name, rtype])["_hsl"].agg(["max", "size"]).rename(columns={"max": "max_hsl_mw", "size": "runs"}).to_string()
          if len(hit) else "  none (all resource types searched)")
    spp = fetch_spp_day(api, day, None)
    locs = sorted({v for v in spp["location"] if any(p in v.upper() for p in pats)})
    print(f"\nSPP settlement points matching {patterns}:")
    print("\n".join(f"  {v}" for v in locs) if locs else "  none")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.WARNING)
    from pipeline.common import load_env

    load_env()
    ap = argparse.ArgumentParser(description="look up ERCOT resource / settlement-point names")
    ap.add_argument("--find", nargs="+", required=True, metavar="TEXT")
    ap.add_argument("--day", type=dt.date.fromisoformat, default=dt.date(2026, 7, 15))
    a = ap.parse_args()
    find(a.find, a.day)
