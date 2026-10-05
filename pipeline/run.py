"""Pipeline entry point.

    python -m pipeline.run --phase 1                  # download + cache USPVDB and EIA-860M
    python -m pipeline.run --phase 2 --county Pecos   # plant master + Pecos hand-check report
    python -m pipeline.run --phase 1-2 --county Pecos
    python -m pipeline.run --phase 3                  # 3a: ERCOT resource lists + EIA↔ERCOT crosswalk
    python -m pipeline.run --phase 4 --layer transmission|gas_pipelines|flood|parcels|fiber [--load]   # geo layers (see pipeline/phase_4_geo)
    python -m pipeline.run --phase 5 [--load]         # scores from plants + metrics + layers → data/scores.parquet
    python -m pipeline.run --phase 3b                 # 3b: SCED + SPP history → curtailment, capture rate
    python -m pipeline.run --phase 3b --since 2026-07-15 --until 2026-07-15   # one-day probe
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging

import geopandas as gpd

from pipeline.common import DATA_DIR, load_env

IMPLEMENTED = {1, 2, 3, 4, 5}
DEFAULT_ALL = [1, 2, 3, 5]   # phase 4 layers download several sources; run them explicitly (--phase 4); 5 rescores from what exists


def _phases(spec: str) -> list[int]:
    if spec == "all":
        return list(DEFAULT_ALL)
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(spec)]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pipeline.run")
    ap.add_argument("--phase", default="all", help="all | N | N-M")
    ap.add_argument("--county", default="", help="write a hand-check report for this county after phase 2")
    ap.add_argument("--since", type=dt.date.fromisoformat, default=None, help="3b: first operating day (YYYY-MM-DD)")
    ap.add_argument("--until", type=dt.date.fromisoformat, default=None, help="3b: last operating day")
    ap.add_argument("--no-fetch", action="store_true", help="3b: compute from cached days only")
    ap.add_argument("--retry-missing", action="store_true", help="3b: re-fetch days ERCOT previously returned no data for")
    ap.add_argument("--layer", default="all", help="4: one geo layer (transmission, gas_pipelines, flood, parcels, fiber) or all")
    ap.add_argument("--load", action="store_true", help="after phase 2 / 4 / 5, write plants / layers / scores to Supabase")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    load_env()

    if args.phase == "3b":
        from pipeline.phase_3_market import market

        market.run(args.since, args.until, fetch=not args.no_fetch, retry_missing=args.retry_missing)
        return

    for n in _phases(args.phase):
        if n not in IMPLEMENTED:
            raise SystemExit(f"phase {n} not implemented yet")
        if n == 1:
            from pipeline.phase_1_ingest import eia860m, eia_annual, uspvdb

            uspvdb.run()
            eia860m.run()
            eia_annual.run_eia860()
            eia_annual.run_eia923()
        elif n == 2:
            from pipeline.phase_2_normalize import county_check, plant_master

            plant_master.run()
            if args.county:
                plants = gpd.read_parquet(plant_master.OUT_PATH)
                orphans = gpd.read_parquet(DATA_DIR / "uspvdb_orphans_tx.parquet")
                path = county_check.report(plants, orphans, args.county)
                logging.getLogger("pipeline").info("hand-check report → %s", path)
            if args.load:
                from pipeline import load_supabase

                load_supabase.run()
        elif n == 5:
            from pipeline.phase_5_score import load as score_load
            from pipeline.phase_5_score import score

            score.run()
            if args.load:
                score_load.run()
        elif n == 4:
            from pipeline.phase_4_geo import load as layer_load
            from pipeline.phase_4_geo import fiber, flood, gas_pipelines, parcels, transmission

            layers = {"transmission": transmission.run, "gas_pipelines": gas_pipelines.run, "flood": flood.run,
                      "parcels": parcels.run, "fiber": fiber.run}
            names = sorted(layers) if args.layer == "all" else [args.layer]
            unknown = [x for x in names if x not in layers]
            if unknown:
                raise SystemExit(f"unknown layer {unknown}; available: {sorted(layers)}")
            for name in names:
                layers[name]()
            if args.load:
                layer_load.run(names)
        elif n == 3:
            from pipeline.phase_1_ingest import ercot
            from pipeline.phase_3_market import crosswalk

            ercot.cdr()  # required: the only source with unit code + county + MW + year
            for step in (ercot.sced_pv, ercot.node_to_unit):  # evidence only; crosswalk runs without them
                try:
                    step()
                except Exception as e:  # network / credentials — say so, keep going
                    logging.getLogger("pipeline").warning("%s failed (%s); crosswalk continues without it",
                                                          step.__name__, e)
            crosswalk.run()


if __name__ == "__main__":
    main()
