"""EIA-860 (annual) and EIA-923 zips → Parquet.

EIA-860: plant file (Grid Voltage (kV)) and Schedule 3_3 solar (tracking, module technology, bifacial), all states.
EIA-923: Page 1 Generation and Fuel Data, all plants (monthly Netgen columns + Respondent Frequency).

Writes data/raw/eia860/{plant,solar}.parquet and data/raw/eia923/generation.parquet, each with a `data_year`
and `source_file` column so downstream output can say which release it came from.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from pipeline.common import download, load_config, manual_file, raw_dir, read_excel_table, zip_member

log = logging.getLogger(__name__)


def _fetch(source: str, today: dt.date) -> tuple[Path, int]:
    """Newest available zip for the source: manual override first, then {year} URLs newest year first."""
    manual = manual_file(source, (".zip",))
    if manual:
        digits = "".join(ch for ch in manual.stem if ch.isdigit())
        year = int(digits[-4:]) if len(digits) >= 4 else today.year - 1
        log.info("using manual %s file %s (data year %d)", source, manual, year)
        return manual, year
    cfg = load_config()["sources"][source]
    errors = []
    for back in cfg["years_back"]:
        year = today.year - back
        for tmpl in cfg["urls"]:
            url = tmpl.format(year=year)
            try:
                return download(url, raw_dir(source) / Path(url).name), year
            except Exception as e:
                errors.append(f"{url}: {e}")
    raise RuntimeError(f"{source} download failed; put the zip in data/raw/{source}/manual/.\n  " + "\n  ".join(errors))


def run_eia860(today: dt.date | None = None) -> list[Path]:
    cfg = load_config()["sources"]["eia860"]
    zpath, year = _fetch("eia860", today or dt.date.today())
    outs = []
    for kind, pattern, sheets in (("plant", cfg["plant_file"], ["Plant"]), ("solar", cfg["solar_file"], cfg["solar_sheets"])):
        member = zip_member(zpath, pattern)
        df = read_excel_table((zpath, member), cfg["key_col"], sheets)
        df["data_year"] = year
        df["source_file"] = f"{zpath.name}/{member}"
        out = raw_dir("eia860") / f"{kind}.parquet"
        df.to_parquet(out, index=False)
        log.info("EIA-860 %d %s: %d rows → %s", year, kind, len(df), out)
        outs.append(out)
    return outs


def run_eia923(today: dt.date | None = None) -> Path:
    cfg = load_config()["sources"]["eia923"]
    zpath, year = _fetch("eia923", today or dt.date.today())
    member = zip_member(zpath, cfg["gen_file"])
    df = read_excel_table((zpath, member), cfg["key_col"], cfg["gen_sheets"])
    df["data_year"] = year
    df["source_file"] = f"{zpath.name}/{member}"
    out = raw_dir("eia923") / "generation.parquet"
    df.to_parquet(out, index=False)
    log.info("EIA-923 %d: %d plant/prime-mover rows → %s", year, len(df), out)
    return out
