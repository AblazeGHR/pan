"""CP-X1 runner regression: collection errors cannot become a green gate."""
import json
import ast
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


def test_terminal_secret_store_native_declarations_are_windows_guarded():
    """Regression for real Linux/macOS collection at ctypes.WinDLL declarations.

    Static import-time boundary only; Windows execution and real POSIX collection
    remain distinct evidence. Do not fake sys.platform to claim a target run.
    """
    tree = ast.parse((gate.ROOT / "tests/test_terminal_secret_store.py").read_text(encoding="utf-8"))

    def import_time(nodes, guarded=False):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.If):
                windows = isinstance(node.test, ast.Name) and node.test.id == "WINDOWS"
                yield from import_time(node.body, guarded or windows)
                yield from import_time(node.orelse, guarded)
                continue
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    if isinstance(child.func.value, ast.Name) and child.func.value.id == "ctypes":
                        if child.func.attr == "WinDLL":
                            yield child.lineno, guarded
                if isinstance(child, ast.Import):
                    if any(name.name == "ctypes.wintypes" for name in child.names):
                        yield child.lineno, guarded

    declarations = list(import_time(tree.body))
    assert len(declarations) == 3  # wintypes and both native libraries stay covered.
    assert all(guarded for _, guarded in declarations), declarations
    assert not any(isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
    ) for node in tree.body), "Do not replace the import fix with a whole-module skip"


def test_plugin_ipc_endpoint_contract_executes_in_fast_gate(tmp_path):
    # CP-X3 owns this file; MA combines it with the CI candidate. Do not make
    # absent files optional: the actual combined gate must fail if it is missing.
    command = gate.phase_command("fast", tmp_path)
    assert command.count("tests/test_plugin_ipc_endpoint.py") == 1
    assert "--collect-only" not in command
    assert not any(arg.startswith(("--ignore", "--deselect")) for arg in command)
    assert "-k" not in command


def test_lifecycle_preparation_nonblocking_and_fence_contracts_are_fast_nodes(tmp_path):
    command = gate.phase_command("fast", tmp_path)
    nodes = (
        "tests/test_lifecycle_fast_contract.py",
        "tests/test_lifecycle_prelaunch_fence.py::test_prelaunch_original_error_idempotence_and_attempt_fence",
        "tests/test_lifecycle_prelaunch_fence.py::test_claim_or_launch_marker_forbids_prelaunch_rejection",
    )
    for node in nodes:
        assert command.count(node) == 1
        assert (gate.ROOT / node.split("::", 1)[0]).is_file()
    assert "--collect-only" not in command and "-k" not in command
    assert not any(arg.startswith(("--ignore", "--deselect")) for arg in command)


def test_lifecycle_successor_mapping_preserves_five_original_reds_and_three_open_controller_gates():
    mapping = json.loads((gate.ROOT / "docs/reports/CP_X1_LIFECYCLE_GATE_MAPPING.json").read_text(encoding="utf-8"))
    rows = mapping["successors"]
    assert len(rows) == 5 and len({row["originalNode"] for row in rows}) == 5
    assert all(row["originalResult"] == "failed" and row["remainingDifference"] for row in rows)
    assert (gate.ROOT / mapping["originalRedEvidence"]).is_file()
    for row in rows:
        original_file, original_name = row["originalNode"].split("::", 1)
        assert original_name in {node.name for node in ast.parse((gate.ROOT / original_file).read_text(encoding="utf-8")).body
                                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in row["fastSuccessorNodes"]:
            path, name = node.split("::", 1)
            assert path in gate.FAST_TESTS
            names = {item.name for item in ast.parse((gate.ROOT / path).read_text(encoding="utf-8")).body
                     if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert name in names
    assert mapping["controllerGates"]["status"] == "open_pending_cpx2_combination"
    assert len(mapping["controllerGates"]["originalNodes"]) == 3
