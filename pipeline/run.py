"""Pipeline entry point.

    python -m pipeline.run --phase 1                  # download + cache USPVDB and EIA-860M
    python -m pipeline.run --phase 2 --county Pecos   # plant master + Pecos hand-check report
    python -m pipeline.run --phase 1-2 --county Pecos
    python -m pipeline.run --phase 3                  # 3a: ERCOT resource lists + EIA↔ERCOT crosswalk
"""
from __future__ import annotations

import argparse
import logging

import geopandas as gpd

from pipeline.common import DATA_DIR, load_env

IMPLEMENTED = {1, 2, 3}


def _phases(spec: str) -> list[int]:
    if spec == "all":
        return sorted(IMPLEMENTED)
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(spec)]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pipeline.run")
    ap.add_argument("--phase", default="all", help="all | N | N-M")
    ap.add_argument("--county", default="", help="write a hand-check report for this county after phase 2")
    ap.add_argument("--load", action="store_true", help="after phase 2, write pass/review plants to Supabase")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    load_env()

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
