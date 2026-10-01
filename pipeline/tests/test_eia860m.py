import openpyxl

from pipeline.phase_1_ingest.eia860m import _candidate_months, _month_from_name, read


def test_read_skips_notes_and_footnotes(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Operating"
    ws.append(["U.S. Energy Information Administration — Preliminary Monthly Electric Generator Inventory"])
    ws.append(["Note: data are preliminary"])
    ws.append(["Entity ID", "Entity Name", "Plant ID", "Plant Name", "Generator ID", "Nameplate Capacity (MW)"])
    ws.append([1, "Op", 60001, "Alpha", 1, 80.0])
    ws.append([1, "Op", 60001, "Alpha", "PV2", 20.0])
    ws.append(["NOTE: footnote row", None, None, None, None, None])
    path = tmp_path / "august_generator2026.xlsx"
    wb.save(path)
    df = read(path, sheet="Operating")
    assert list(df["Plant ID"]) == [60001, 60001]
    assert list(df["Generator ID"]) == ["1", "PV2"]
    df.to_parquet(tmp_path / "x.parquet")  # mixed-type columns must be arrow-safe


def test_month_helpers():
    import datetime as dt
    assert _month_from_name("august_generator2026.xlsx") == "2026-08"
    assert list(_candidate_months(dt.date(2026, 2, 15), 2)) == [(2026, 2), (2026, 1), (2025, 12)]


def test_download_rejects_html_error_page(tmp_path, monkeypatch):
    import pytest
    import requests

    from pipeline import common

    class FakeResp:
        def __init__(self):
            self.status_code = 200
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def raise_for_status(self):
            pass
        def iter_content(self, chunk_size):
            yield b"<!DOCTYPE html><html>Page not found</html>"

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp())
    dest = tmp_path / "september_generator2026.xlsx"
    with pytest.raises(ValueError, match="not a zip"):
        common.download("https://example.invalid/x.xlsx", dest)
    assert not dest.exists() and not (tmp_path / "september_generator2026.xlsx.part").exists()
