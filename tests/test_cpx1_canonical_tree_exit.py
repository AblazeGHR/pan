"""Actual canonical runner: root exit and an owned wait error."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from packages.core.terminal.identity import ProcessStatus, close_handle_checked, wait_state
from packages.jobs.independent_executor import ExecutionStore
from packages.jobs.process_logs import read_log
from tests.test_job_final_independent_execution import admit, attempt
from tests.test_job_lifecycle_prevention import desktop_gone, kill, retain, until

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native Job and desktop proof")


@pytest.mark.parametrize("fault", ["root-exit", "wait-error"])
def test_canonical_runner_tree_exit_and_error(tmp_path, fault):
    store = ExecutionStore(tmp_path / "execution")
    tree = Path(__file__).parent / "helpers" / "lifecycle_tree_probe.py"
    plan = {"operation": "process", "argv": [sys.executable, str(tree), str(tmp_path), "2"], "cwd": str(tmp_path)}
    doc, created = admit(store, plan)
    assert created
    helper = Path(__file__).parent / "support" / "cpx1_canonical_fault_runner.py"
    runner = subprocess.Popen([sys.executable, str(helper), str(store.root), doc["effectId"], str(tmp_path), fault],
                              cwd=str(Path(__file__).resolve().parents[1]), stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    handles = []
    try:
        assert until(lambda: all((tmp_path / f"tree-{i}.json").exists() for i in range(3)), timeout=20)
        rows = [json.loads((tmp_path / f"tree-{i}.json").read_text()) for i in range(3)]
        handles = [retain(row["pid"], row["creationFiletime"]) for row in rows]
        assert len({row["desktop"] for row in rows}) == 1
        desktop = rows[0]["desktop"]
        if fault == "root-exit":
            (tmp_path / "root-release").touch()
        assert runner.wait(timeout=20) == 0
        result = store.get(doc["effectId"])
        assert result["attempt"] == attempt() and result["effectKey"] == doc["effectKey"]
        assert result["result"] and len(store.events()["events"]) == 1
        assert until(lambda: all(wait_state(h) is ProcessStatus.DEAD for h in handles), timeout=10)
        assert until(lambda: desktop_gone(desktop), timeout=10)
        if fault == "root-exit":
            assert result["result"]["outcome"] == "succeeded"
            log = read_log(store.root, result)["text"]
            assert "owned root stdout" in log and "owned root stderr" in log
        else:
            assert result["result"]["outcome"] == "unknown"
            assert result["result"]["receipt"]["code"] == "process_exception"
            assert "injected owned wait failure" in result["result"]["receipt"]["message"]
            assert result["result"]["cleanupProof"]["confirmed"] is False
    finally:
        if runner.poll() is None:
            runner.terminate()
            runner.wait(timeout=10)
        for handle in handles:
            if wait_state(handle) is not ProcessStatus.DEAD:
                kill(handle)
            close_handle_checked(handle)
