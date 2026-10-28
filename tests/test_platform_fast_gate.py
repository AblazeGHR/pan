"""CP-X1 runner regression: collection errors cannot become a green gate."""
import json
import sys

import pytest

from scripts import platform_fast_gate as gate


def test_collection_covers_every_repository_pytest_root_without_suppression(tmp_path):
    command = gate.phase_command("collect", tmp_path)
    assert command[:4] == [sys.executable, str(gate.ROOT / "scripts/run_tests_isolated.py"),
                           "--", sys.executable]
    assert ["tests/", "packages/qq/", "packages/wechat/"] == command[6:9]
    assert "--collect-only" in command
    assert not any(arg.startswith(("--ignore", "--deselect", "--continue-on-collection-errors"))
                   for arg in command)
    assert "-k" not in command and "-m" not in command[6:]


def test_real_child_collection_failure_stays_red_and_fast_evidence_survives(tmp_path, monkeypatch):
    def command(phase, output):
        code = 2 if phase == "collect" else 0
        return [sys.executable, "-c", f"print('{phase} evidence'); raise SystemExit({code})"]

    monkeypatch.setattr(gate, "phase_command", command)
    assert gate.run_gate(gate.PHASES, tmp_path) == 2
    report = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert report["platform"] == sys.platform
    assert [(row["phase"], row["exitCode"]) for row in report["results"]] == [
        ("import", 0), ("collect", 2), ("fast", 0)]
    for phase in gate.PHASES:
        assert (tmp_path / f"{phase}.log").read_text(encoding="utf-8").strip() == f"{phase} evidence"


def test_unknown_phase_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown phase"):
        gate.phase_command("subset-collection", tmp_path)


def test_integrated_worker_quota_and_log_contracts_execute_in_fast_gate(tmp_path):
    command = gate.phase_command("fast", tmp_path)
    for target in ("tests/test_worker_platform_admission.py",
                   "tests/test_quota_native_admission.py",
                   "tests/test_job_process_logs.py"):
        assert command.count(target) == 1
        assert (gate.ROOT / target).is_file()
    assert "--collect-only" not in command
    assert not any(arg.startswith(("--ignore", "--deselect")) for arg in command)
    assert "-k" not in command
