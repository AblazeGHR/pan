"""Bind focused results to committed source; redact only JUnit hostnames."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
SOURCE = "15e34ba63fcb92dcf77ff0ff563dcb886fdf9730"
BASE = "353bb52af7806bf7373563c3baae7dd5b66c166c"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main():
    paths = git("diff", "--name-only", BASE, SOURCE).decode().splitlines()
    assert len(paths) == 7
    product = {}
    for name in paths:
        raw = git("show", SOURCE + ":" + name)
        assert raw.replace(b"\r\n", b"\n") == (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        entry = {"gitBlob": git("rev-parse", SOURCE + ":" + name).decode().strip(),
                 "sha256GitBytes": hashlib.sha256(raw).hexdigest()}
        if name.endswith(".py"):
            entry["symbols"] = {}
            def visit(node, prefix=""):
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        label = prefix + child.name
                        entry["symbols"][label] = {"start": child.lineno, "end": child.end_lineno}
                        visit(child, label + ".")
                    else:
                        visit(child, prefix)
            visit(ast.parse(raw.decode("utf-8-sig")))
        product[name] = entry
    unchanged = ["packages/jobs/final_runtime/ledger.py", "packages/jobs/final_runtime/coordinator.py",
                 "packages/jobs/independent_executor.py", "tests/test_background_job_retry_if.py",
                 "tests/test_authorization_runtime.py", "tests/automation_retirement.json"]
    assert not git("diff", "--name-only", BASE, SOURCE, "--", *unchanged).strip()
    results = {}
    final_nodes = set()
    for path in sorted(OUT.glob("*.xml")):
        tree = ET.parse(path)
        for suite in tree.getroot().iter("testsuite"):
            suite.set("hostname", "owned-isolated-test-host")
        tree.write(path, encoding="utf-8", xml_declaration=True)
        cases = list(tree.getroot().iter("testcase"))
        failures = [c.attrib for c in cases if c.find("failure") is not None or c.find("error") is not None]
        skipped = [{**c.attrib, "reason": c.find("skipped").get("message")} for c in cases if c.find("skipped") is not None]
        results[path.name] = {"tests": len(cases), "failedOrError": len(failures), "skipped": skipped,
                              "passed": len(cases) - len(failures) - len(skipped),
                              "failedNodes": failures, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        if path.name.startswith("source-15e34ba6-"):
            assert not failures and not skipped
            final_nodes.update((c.get("classname"), c.get("name")) for c in cases)
    assert len(final_nodes) == 349
    proof = json.loads((OUT / "default-detached-proof.json").read_text(encoding="utf-8"))
    assert (proof["actualInvocations"], proof["runs"], proof["attempts"], proof["distinctEffects"]) == (2, 1, 2, 1)
    assert proof["originalCleanupComplete"] and proof["originalSupervisorExited"] and proof["ownedPortReleased"]
    result = {"sourceSHA": SOURCE, "proposalBaseSHA": BASE, "acceptedAncestorSHA": "aa76b83e16ac580d8a548752011b7c81636c6519",
              "interpreter": "D:/project/Pan-main/.venv/Scripts/python.exe", "wrapper": "scripts/run_tests_isolated.py",
              "platformVerified": "Windows only", "sourceFiles": product, "unchangedBoundaries": unchanged,
              "finalSourceUniquePassed": len(final_nodes), "junitResults": results, "defaultDetached": proof,
              "unverified": ["Linux/macOS runtime", "remote CI", "full suite", "browser", "real provider/data migration",
                  "deployment", "24-hour detached timeout (retained-owner timeout proof was tested directly)"],
              "oldReds": "Original 65 retired retryIf tests unchanged and not rerun; not declared passing or closed."}
    (OUT / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sourceSHA": SOURCE, "changedSourceFiles": len(product), "uniquePassedAtSource": len(final_nodes),
                      "unchangedSafetyBoundaries": len(unchanged), "ownedHTTPPortReleased": proof["ownedPortReleased"]}))


if __name__ == "__main__":
    main()
