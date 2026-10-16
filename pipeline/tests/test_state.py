"""Pipeline state sync: file selection, upload/download round trip against a fake Storage API, and the guard when empty."""
import json

import pytest

from pipeline import state
from pipeline.run import _phases


class Resp:
    def __init__(self, content=b"", status=200):
        self.content, self.status_code, self.text = content, status, content.decode(errors="ignore")[:200]

    @property
    def ok(self):
        return self.status_code < 400


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def post(self, url, data=None, headers=None, **kw):
        assert headers["x-upsert"] == "true" and "apikey" in headers
        self.objects[url.split(f"/{state.BUCKET}/")[1]] = data
        return Resp()

    def get(self, url, headers=None, **kw):
        k = url.split(f"/{state.BUCKET}/")[1]
        return Resp(self.objects[k]) if k in self.objects else Resp(b'{"error":"not_found"}', 404)


def build(tmp_path):
    d = tmp_path / "data"
    (d / "layers").mkdir(parents=True)
    (d / "layers" / "fiber.parquet").write_bytes(b"AAA")
    (d / "layers" / "notes.txt").write_bytes(b"skip")
    (d / "plant_metrics.parquet").write_bytes(b"BBBB")
    (d / "plants.parquet").write_bytes(b"not state")
    return d


def test_state_files_are_layers_and_metrics_only(tmp_path):
    assert [p.name for p in state.state_files(build(tmp_path))] == ["fiber.parquet", "plant_metrics.parquet"]


def test_push_then_pull_round_trip(tmp_path):
    src, dst, fake = build(tmp_path), tmp_path / "ci", FakeStorage()
    m = state.push("https://x.supabase.co", "sb_secret_k", src, fake)
    assert m["files"] == {"layers/fiber.parquet": 3, "plant_metrics.parquet": 4}
    assert json.loads(fake.objects["manifest.json"])["files"] == m["files"]
    state.pull("https://x.supabase.co", "sb_secret_k", dst, fake)
    assert (dst / "layers" / "fiber.parquet").read_bytes() == b"AAA" and (dst / "plant_metrics.parquet").read_bytes() == b"BBBB"
    assert not (dst / "plants.parquet").exists()


def test_pull_without_state_stops_and_truncated_download_is_rejected(tmp_path):
    with pytest.raises(SystemExit, match="no pipeline state"):
        state.pull("https://x.supabase.co", "sb_secret_k", tmp_path / "ci", FakeStorage())
    src, fake = build(tmp_path), FakeStorage()
    state.push("https://x.supabase.co", "sb_secret_k", src, fake)
    fake.objects["plant_metrics.parquet"] = b"BB"
    with pytest.raises(RuntimeError, match="expected 4 bytes"):
        state.pull("https://x.supabase.co", "sb_secret_k", tmp_path / "ci2", fake)


def test_phase_list():
    assert _phases("1,2,5") == [1, 2, 5] and _phases("1-2") == [1, 2] and _phases("5") == [5]
