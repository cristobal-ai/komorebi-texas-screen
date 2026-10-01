"""Shared paths, config loading and cached downloads for every phase."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_DIR = REPO_ROOT / "pipeline"
DATA_DIR = Path(os.environ.get("KOMOREBI_DATA_DIR", REPO_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
CONFIG_PATH = PIPELINE_DIR / "config.yaml"

log = logging.getLogger("pipeline")


@lru_cache(maxsize=1)
def load_config(path: Path | str = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_env() -> None:
    """Load pipeline/.env if present; CI provides the same names as real env vars."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(PIPELINE_DIR / ".env", override=False)


def raw_dir(source: str) -> Path:
    d = RAW_DIR / source
    d.mkdir(parents=True, exist_ok=True)
    return d


def manual_file(source: str, suffixes: tuple[str, ...]) -> Path | None:
    """Newest hand-downloaded file in data/raw/<source>/manual/, if any. Overrides the URL download."""
    d = RAW_DIR / source / "manual"
    if not d.is_dir():
        return None
    files = sorted((p for p in d.iterdir() if p.suffix.lower() in suffixes), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


# .xlsx and .zip are both zip containers; anything else (e.g. an HTML "page not found" served with 200) is rejected.
ZIP_MAGIC = b"PK\x03\x04"
ZIP_SUFFIXES = (".zip", ".xlsx")


def download(url: str, dest: Path, timeout: tuple[int, int] = (20, 60)) -> Path:
    """Download url to dest unless dest already exists (the raw cache is never re-fetched implicitly).

    timeout = (connect s, seconds without receiving a byte). Zip-based files are checked for the zip signature.
    """
    if dest.exists() and dest.stat().st_size > 0:
        log.info("cache hit %s", dest)
        return dest
    log.info("GET %s", url)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=timeout, headers={"User-Agent": "komorebi-texas-screen/0.1"}) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        if dest.suffix.lower() in ZIP_SUFFIXES:
            with open(tmp, "rb") as f:
                head = f.read(4)
            if head != ZIP_MAGIC:
                raise ValueError(f"not a zip/xlsx (starts with {head!r}; likely an HTML error page)")
    except Exception as e:
        tmp.unlink(missing_ok=True)
        log.warning("failed %s: %s", url, e)
        raise
    tmp.replace(dest)
    log.info("saved %s (%.1f MB)", dest, dest.stat().st_size / 1e6)
    return dest


# ---- Excel helpers shared by the EIA readers ------------------------------------------------------------------

def norm(name) -> str:
    """'Grid Voltage (kV)', 'Grid\\nVoltage (kV)' and 'grid_voltage_kv' all → 'gridvoltagekv'."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def find_col(df, *candidates: str, required: bool = False, what: str = "") -> str | None:
    """First column whose normalized name equals, else starts with, one of the candidates (tried in order).

    EIA renames columns between releases; a miss logs the columns that do exist so the fix is one edit.
    """
    by_norm = {norm(c): c for c in df.columns}
    for cand in candidates:
        if norm(cand) in by_norm:
            return by_norm[norm(cand)]
    for cand in candidates:
        hits = [c for n, c in by_norm.items() if n.startswith(norm(cand))]
        if hits:
            return hits[0]
    msg = f"column not found for {what or candidates[0]!r} (tried {list(candidates)}); available: {list(df.columns)}"
    if required:
        raise KeyError(msg)
    log.warning(msg)
    return None


def read_excel_table(src, key_col: str, sheets: list[str] | None = None, probe_rows: int = 15):
    """Read the sheet whose header row contains key_col; EIA prepends title/notes rows, so the header row is found.

    src: path, or a zip path + member as (zip_path, member_name). sheets: preferred sheet names, tried first.
    Rows whose key_col is not numeric (footnotes, state totals) are dropped; object columns become strings.
    """
    import io
    import zipfile

    import pandas as pd

    if isinstance(src, tuple):
        zpath, member = src
        with zipfile.ZipFile(zpath) as z:
            data = io.BytesIO(z.read(member))
    else:
        data = src
    xl = pd.ExcelFile(data)
    order = [s for s in (sheets or []) if s in xl.sheet_names] + [s for s in xl.sheet_names if s not in (sheets or [])]
    key = norm(key_col)
    for sheet in order:
        probe = xl.parse(sheet, header=None, nrows=probe_rows)
        hits = [i for i, row in probe.iterrows() if any(norm(v) == key for v in row.tolist())]
        if not hits:
            continue
        df = xl.parse(sheet, header=hits[0])
        df.columns = [" ".join(str(c).split()) for c in df.columns]  # collapse EIA's embedded line breaks
        kc = find_col(df, key_col, required=True)
        df = df[pd.to_numeric(df[kc], errors="coerce").notna()].copy()
        df[kc] = pd.to_numeric(df[kc]).astype(int)
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(lambda v: None if pd.isna(v) else str(v).strip())
        log.info("read %s sheet %r: %d rows", src if not isinstance(src, tuple) else src[1], sheet, len(df))
        return df
    raise ValueError(f"no sheet with a {key_col!r} header in {src} (sheets: {xl.sheet_names})")


def zip_member(zpath, *patterns: str) -> str:
    """Name of the first member of zpath whose lower-cased name contains every pattern."""
    import zipfile

    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if n.lower().endswith((".xlsx", ".xls"))]
    for n in names:
        if all(p.lower() in n.lower() for p in patterns):
            return n
    raise FileNotFoundError(f"no member matching {patterns} in {zpath}: {names}")
