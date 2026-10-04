"""Regression witnesses for testing from a configured Pan/Job parent process."""
import json
import os
from pathlib import Path
import subprocess
import sys

from packages.core import background_jobs as jobs
from packages.core import config
from packages.scheduler import store
from packages.core.terminal import registry
from scripts.run_tests_isolated import test_environment as build_test_environment


REPO = Path(__file__).resolve().parents[1]


def test_environment_filters_all_pan_settings_without_changing_parent(tmp_path):
    parent = {"PATH": "toolchain", "SYSTEMROOT": "system", "PYTHONPATH": "wrong-code",
              "PAN_BACKGROUND_JOBS_DIR": "real-jobs", "PAN_SCHEDULER_DIR": "real-scheduler",
              "PAN_TERMINALS_DIR": "real-terminal", "PAN_AGENT_SESSION_ID": "real-session",
              "PAN_PORT": "8768", "PAN_FUTURE_SETTING": "future", "pan_lowercase": "lower"}
    before = dict(parent)
    child = build_test_environment(parent, tmp_path)
    assert parent == before
    assert child["PATH"] == "toolchain" and child["SYSTEMROOT"] == "system"
    assert child["PYTHONPATH"] == str(tmp_path.resolve())
    assert {key for key in child if key.upper().startswith("PAN_")} == {"PAN_TEST_HTTP_PORT"}
    assert 0 < int(child["PAN_TEST_HTTP_PORT"]) < 65536


def test_storage_probe_uses_only_fixture_roots(tmp_path):
    assert jobs._root() == tmp_path / "background_jobs"
    assert store.data_root() == tmp_path / "background_jobs"
    assert registry._resolve_root(None) == tmp_path / "terminals"
    assert config.CONFIG_FILE == tmp_path / "config.json"
    # These real writes must never follow a caller's configured application root.
    jobs._save({"jobId": "job_isolation_probe", "status": "completed", "createdAt": 1})
    config.save_config({"probe": True})
    assert (tmp_path / "background_jobs/jobs/job_isolation_probe.json").exists()
    assert json.loads(config.CONFIG_FILE.read_text(encoding="utf-8"))["probe"] is True


def test_direct_pytest_from_poisoned_parent_keeps_decoy_store_unchanged(tmp_path):
    decoy = tmp_path / "decoy-production"
    decoy.mkdir()
    sentinel = decoy / "sentinel.json"
    sentinel.write_bytes(b'{"must":"remain unchanged"}')
    before = {str(p.relative_to(decoy)): p.read_bytes() for p in decoy.rglob("*") if p.is_file()}
    env = dict(os.environ, PYTHONPATH=str(REPO), PAN_BACKGROUND_JOBS_DIR=str(decoy),
               PAN_SCHEDULER_DIR=str(decoy), PAN_TERMINALS_DIR=str(decoy), PAN_PORT="8768")
    result = subprocess.run([sys.executable, "-m", "pytest", str(Path(__file__).resolve()),
                             "-o", "addopts=", "-q", "-k", "test_storage_probe"],
                            cwd=REPO, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    after = {str(p.relative_to(decoy)): p.read_bytes() for p in decoy.rglob("*") if p.is_file()}
    assert after == before
