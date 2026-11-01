"""Offline SHA/scope/result binding; no provider, service or data access."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "4b577633d2d9ffcdc5bf8a44b12836724c18804e"
SOURCE = "e48926a5e7d6d2bbe1bdcfb405b8a481e94b5abf"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def symbols(raw):
    text = raw.decode("utf-8-sig")
    found = {}
    def visit(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                key = prefix + child.name
                found[key] = {"start":child.lineno, "end":child.end_lineno,
                              "text":ast.get_source_segment(text, child)}
                visit(child, key + ".")
            else:
                visit(child, prefix)
    visit(ast.parse(text))
    return found


def main():
    paths = git("diff", "--name-only", BASE, SOURCE).decode().splitlines()
    assert set(paths) == {"packages/jobs/final_service.py", "packages/actions/typed_executor.py",
                          "tests/test_job_automatic_recovery.py", "tests/test_job_recovery_stable_reset.py"}
    source = {}
    for name in paths:
        raw = git("show", SOURCE + ":" + name)
        assert raw.replace(b"\r\n", b"\n") == (ROOT/name).read_bytes().replace(b"\r\n", b"\n")
        anchors = symbols(raw)
        source[name] = {"sha256GitBytes":hashlib.sha256(raw).hexdigest(),
                        "gitBlob":git("rev-parse", SOURCE+":"+name).decode().strip(),
                        "symbols":{k:{"start":v["start"], "end":v["end"]} for k,v in anchors.items()}}
        if name.startswith("packages/"):
            old = symbols(git("show", BASE+":"+name))
            changed = {k for k,v in anchors.items() if not isinstance(ast.parse(v["text"]).body[0], ast.ClassDef)
                       and (k not in old or v["text"] != old[k]["text"])}
            allowed = ({"TypedExecutor._process"} if "typed_executor" in name else {
                "FinalService._references", "FinalService._stable_execution_seconds", "FinalService._automatic_budget",
                "FinalService._schedule_automatic", "FinalService._claim_automatic", "FinalService._drive_run",
                "FinalService.recover_external_receipts"})
            assert changed <= allowed, changed
            source[name]["changedMethods"] = sorted(changed)
    boundaries = ["packages/jobs/final_runtime", "packages/jobs/independent_executor.py",
        "packages/jobs/independent_execution_runner.py", "packages/core/platform", "packages/core/worker.py",
        "packages/mcp", "scripts/job_final_migration", "packages/jobs/final_contracts.py",
        "packages/jobs/schemas/final", "tests/test_background_job_retry_if.py",
        "tests/test_job_completion_recovery.py", "tests/test_authorization_runtime.py", "tests/automation_retirement.json"]
    assert not git("diff", "--name-only", BASE, SOURCE, "--", *boundaries).strip()
    history = {}
    for name, names in {
        "packages/jobs/action_runtime.py":["Runtime.finalize", "Runtime.execute"],
        "packages/jobs/action_templates.py":["completion_policy"],
        "packages/actions/provider.py":["execute"],
    }.items():
        raw = git("show", BASE+":"+name)
        anchors = symbols(raw)
        history[name] = {"sha256":hashlib.sha256(raw).hexdigest(),
            "symbols":{k:{"start":anchors[k]["start"],"end":anchors[k]["end"]} for k in names}}
    results, nodes = {}, set()
    for path in sorted(OUT.glob("*.xml")):
        raw = path.read_bytes()
        tree = ET.fromstring(raw)
        for suite in tree.iter("testsuite"):
            suite.set("hostname", "owned-isolated-test-host")
        ET.ElementTree(tree).write(path, encoding="utf-8", xml_declaration=True)
        cases = list(tree.iter("testcase"))
        failed = [c.attrib for c in cases if c.find("failure") is not None or c.find("error") is not None]
        skips = [c.attrib for c in cases if c.find("skipped") is not None]
        results[path.name] = {"collected":len(cases), "passed":len(cases)-len(failed)-len(skips),
            "failuresOrErrors":failed, "skipped":skips, "sha256Redacted":hashlib.sha256(path.read_bytes()).hexdigest(),
            "exitCode":1 if failed else 0}
        if path.name.startswith("source-e48926a5-"):
            assert not failed and not skips
            nodes.update((c.get("classname"),c.get("name")) for c in cases)
    assert len(nodes) == 381
    proof = json.loads((OUT/"default-detached.json").read_text(encoding="utf-8"))
    assert (proof["actualInvocations"],proof["maxAttempts"],proof["generations"],proof["distinctEffectKeys"]) == (3,2,[1,2,3],1)
    assert proof["coldReopen"] and proof["ownedPortReleased"] and DecimalSafe(proof["originalStableElapsed"])
    commands = ["D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- "
        "D:/project/Pan-main/.venv/Scripts/python.exe -m pytest " + group + " -q --tb=short --junitxml=evidence/job-recovery-r4/" + output
        for group,output in [
            ("tests/test_job_recovery_stable_reset.py tests/test_job_automatic_recovery.py tests/test_job_final_executors.py tests/test_automation_retry_parallel.py", "source-e48926a5-recovery.xml"),
            ("tests/test_job_final_independent_execution.py tests/test_job_final_http_service.py tests/test_job_final_contracts.py", "source-e48926a5-contract-host.xml")]]
    result = {"baseline":BASE,"sourceSHA":SOURCE,"platform":"Windows only","python":"3.14.5",
        "commands":commands,"source":source,"unchangedBoundaries":boundaries,"originalSource":history,
        "finalSourceUniquePassed":len(nodes),"newStableResetCases":sum(c == "tests.test_job_recovery_stable_reset" for c,n in nodes),
        "results":results,"defaultDetachedProof":proof,"monitoring":"UNIMPLEMENTED: trusted declared completion signal path missing; explicit unsupported",
        "maCoordination":{"clientMessageId":"recovery-r4-monitoring-symbol-coordination-20261029",
            "queueItemId":"q_c8f68e445067418998a5cbc56e9d80c2","receipt":"accepted; consumption unconfirmed, not scope approval"},
        "unverified":["monitoring success", "Linux/macOS", "remote CI", "full suite", "browser", "real-data migration", "deployment", "human acceptance"]}
    (OUT/"verification.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"sourceSHA":SOURCE,"uniquePassed":len(nodes),"newStableResetCases":result["newStableResetCases"],
                      "productFiles":2,"unchangedBoundaries":len(boundaries)}))


def DecimalSafe(value):
    from decimal import Decimal
    return Decimal(value) >= Decimal("2")


if __name__ == "__main__":
    main()
