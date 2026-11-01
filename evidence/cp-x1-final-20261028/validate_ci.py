"""Local structural/dependency checks; PyYAML is an existing local tool only."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = "41a5ffd0b62787f8d7eb97f0a801921d0db31a54"
OUT = Path(__file__).resolve().parent


def baseline(path):
    return subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT)


def targets(source):
    tree = ast.parse(source)
    return next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and any(
                    isinstance(name, ast.Name) and name.id == "FAST_TESTS" for name in node.targets))


def main():
    workflow = yaml.load((ROOT / ".github/workflows/test.yml").read_text(), Loader=yaml.BaseLoader)
    original = yaml.load(baseline(".github/workflows/test.yml"), Loader=yaml.BaseLoader)
    assert workflow["on"] == original["on"]
    assert workflow["jobs"]["pytest"] == original["jobs"]["pytest"]
    assert workflow["jobs"]["platform-fast"] == original["jobs"]["platform-fast"]
    new = targets((ROOT / "scripts/platform_fast_gate.py").read_text(encoding="utf-8"))
    old = targets(baseline("scripts/platform_fast_gate.py").decode())
    added = [node for node in new if node not in old]
    assert added == ["tests/test_worker_platform_admission.py", "tests/test_quota_native_admission.py",
                     "tests/test_job_process_logs.py"]
    assert tuple(node for node in new if node not in added) == old
    slow = workflow["jobs"]["ubuntu-browser"]
    assert slow["runs-on"] == "ubuntu-latest" and slow["timeout-minutes"] == "20"
    assert "needs" not in slow and "continue-on-error" not in slow
    steps = slow["steps"]
    assert steps[0]["uses"] == "actions/checkout@v4"
    prepare = steps[1]["run"]
    assert "rm -rf -- artifacts/notification-error-exclusions/http artifacts/history-terminal/http" in prepare
    for action, version in (("actions/setup-python@v5", "3.12"), ("actions/setup-node@v4", "24"),
                            ("pnpm/action-setup@v4", "9.7.0")):
        step = next(s for s in steps if s.get("uses") == action)
        assert version in step["with"].values()
    build = next(s for s in steps if s.get("name") == "Build production React bundle")
    assert build["working-directory"] == "packages/web"
    assert build["run"].splitlines() == ["pnpm install --frozen-lockfile", "pnpm build"]
    install = next(s for s in steps if "--with-deps --only-shell chromium" in s.get("run", ""))
    test = next(s for s in steps if "tests/test_notification_http.py" in s.get("run", ""))
    assert test["shell"] == "bash" and "scripts/run_tests_isolated.py -- python -m pytest" in test["run"]
    assert "tests/test_history_terminal_http.py" in test["run"]
    assert "| tee evidence/cp-x1-ubuntu-browser/pytest.log" in test["run"]
    assert test["env"]["PLAYWRIGHT_BROWSERS_PATH"] == install["env"]["PLAYWRIGHT_BROWSERS_PATH"]
    assert steps[-1]["if"] == "always()" and steps[-1]["with"]["name"] == "ubuntu-browser-py312"
    assert not any("continue-on-error" in s for s in steps)
    dependency_step = next(s for s in steps if "pip install" in s.get("run", ""))
    assert "playwright==1.63.0" in dependency_step["run"]
    lock = (ROOT / "packages/web/pnpm-lock.yaml").read_text(encoding="utf-8")
    assert "'@playwright/test@1.63.0':" in lock and "playwright@1.63.0:" in lock
    protected = ["tests/test_quota_policy_http.py", "tests/test_job_retirement_quota_transport.py",
                 "evidence/quota-native/original-red.log"]
    hashes = {}
    for path in protected:
        data = (ROOT / path).read_bytes()
        # Compare Git-clean content, respecting this Windows checkout's CRLF.
        actual = subprocess.check_output(["git", "hash-object", "--path", path, path],
                                         cwd=ROOT, text=True).strip()
        expected = subprocess.check_output(["git", "rev-parse", f"{BASE}:{path}"],
                                           cwd=ROOT, text=True).strip()
        assert actual == expected, path
        hashes[path] = {"gitBlob": actual, "workingTreeBytesSha256": hashlib.sha256(data).hexdigest()}
    report = {"baseline": BASE, "sourceSha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "exitCode": 0,
        "existingTriggersAndJobsUnchanged": True, "existingFastNodesPreserved": len(old),
        "addedExecutionFiles": added, "protectedRedInputsSha256": hashes,
        "browserInstallMatchesNodeLock": "1.63.0", "remoteCi": "not_run"}
    (OUT / "workflow-validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
