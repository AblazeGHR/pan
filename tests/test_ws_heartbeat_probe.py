"""Windows heartbeat evidence writes survive brief concurrent readers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


PROBE_PATH = Path(__file__).parent / "support" / "ws_heartbeat_probe.py"
SPEC = importlib.util.spec_from_file_location("ws_heartbeat_probe", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_sample_snapshot_retries_transient_windows_replace_denial(tmp_path, monkeypatch):
    target = tmp_path / "heartbeat-samples.json"
    target.write_text('[{"at": 1}]', encoding="utf-8")
    real_replace = probe.os.replace
    attempts = 0

    def replace_with_reader_lock(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise PermissionError("simulated concurrent reader")
        real_replace(source, destination)

    monkeypatch.setattr(probe.os, "replace", replace_with_reader_lock)
    latest = [{"at": 2, "rttMs": 1.0, "gapMs": 20.0}]
    probe._write_samples(target, latest)

    assert attempts == 3
    assert json.loads(target.read_text(encoding="utf-8")) == latest
    assert not target.with_name(target.name + ".tmp").exists()
