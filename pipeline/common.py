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
