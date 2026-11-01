"""Summarize the public-cancel delivery without overwriting original red evidence."""
from collections import Counter
from pathlib import Path
import ast
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
BASE = "bdd4c778d55daff58529635b2adc464c03a1393c"
PRIOR = "a700f2209942a27cd9af4af2ac1b141a53295975"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def method(source, class_name, method_name):
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == class_name)
    return ast.dump(next(n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and n.name == method_name), include_attributes=False)


def main():
    commands = []
    for path in sorted(OUT.glob("*-command.json")):
        entry = json.loads(path.read_text(encoding="utf-8"))
        xml = OUT / (entry["label"] + ".xml")
        if xml.exists():
            nodes = []
            for case in ET.parse(xml).getroot().findall(".//testcase"):
                state = next((s for s in ("failure", "error", "skipped") if case.find(s) is not None), "passed")
                detail = case.find(state)
                nodes.append({"node": case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"],
                              "status": state, **({"message": detail.attrib.get("message", "")} if detail is not None else {})})
            entry.update(testcases=len(nodes), counts=dict(Counter(n["status"] for n in nodes)), nodes=nodes)
        commands.append(entry)
    backend = next(c for c in commands if c["label"] == "backend-delivery")
    venv = next(c for c in commands if c["label"] == "venv-delivery")
    assert backend["exit"] == 1 and backend["counts"] == {"passed": 513, "failure": 3, "skipped": 19}
    assert venv["exit"] == 0 and venv["counts"] == {"passed": 26}
    assert next(c for c in commands if c["label"] == "import-delivery")["exit"] == 0
    expected_red = {
        "tests/test_job_delete_safety.py::test_scheduled_terminal_child_writeback_does_not_recreate_deleted_parent",
        "tests/test_job_delete_http_owned.py::test_real_owned_tree_delete_http[False]",
        "tests/test_job_delete_http_owned.py::test_real_owned_tree_delete_http[True]"}
    assert {n["node"] for n in backend["nodes"] if n["status"] == "failure"} == expected_red
    initial = json.loads((OUT.parent / "validation.json").read_text(encoding="utf-8"))
    authorized = {"packages/jobs/final_service.py", "packages/jobs/final_api.py", "packages/jobs/final_runtime/ledger.py"}
    protected = []
    for row in initial["protectedFiles"]:
        path = row["path"]
        if path in authorized:
            continue
        blob = git("hash-object", "--path=" + path, path)
        assert blob == row["baselineBlob"], path
        protected.append({"path": path, "baselineBlob": blob, "unchanged": True})
    ast_checks = []
    for path, cls, name in (("packages/jobs/final_service.py", "FinalService", "actions"),
                            ("packages/jobs/final_runtime/ledger.py", "Ledger", "finish_attempt")):
        before = git("show", PRIOR + ":" + path)
        current = (ROOT / path).read_text(encoding="utf-8")
        assert method(before, cls, name) == method(current, cls, name)
        ast_checks.append({"path": path, "method": cls + "." + name, "unchangedFromPrior": True})
    original_paths = git("ls-tree", "-r", "--name-only", PRIOR, "evidence/p23-retained-safety", "evidence/r6i-repair").splitlines()
    original_evidence = []
    for path in original_paths:
        original = git("rev-parse", PRIOR + ":" + path)
        assert git("hash-object", "--path=" + path, path) == original, path
        original_evidence.append({"path": path, "blob": original, "unchanged": True})
    resource_checks = []
    for axis in ("native", "venv"):
        for path in sorted((OUT / axis).glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["portReleased"] and data["ownedProcessesRemaining"] == []
            assert data.get("canonicalPortReleased", True) and data.get("productProcessesRemaining", []) == []
            if path.stem in {"canonical-delete-unknown-false", "canonical-delete-unknown-true"}:
                assert data["canonicalCleanupRequested"] is True and data["cleanupCalls"] == data["effects"] == 1
            resource_checks.append({"artifact": str(path.relative_to(OUT)).replace("\\", "/"), "cleanupVerified": True})
    # Snapshot raw diagnostics before review-only whitespace normalization.
    whitespace = []
    with zipfile.ZipFile(OUT / "raw-output.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted([*OUT.glob("*.log"), *OUT.glob("*.xml")]):
            raw = path.read_bytes()
            archive.writestr(path.name, raw)
            clean = ("\n".join(line.rstrip() for line in raw.decode("utf-8", errors="replace").splitlines()).rstrip() + "\n").encode("utf-8")
            path.write_bytes(clean)
            whitespace.append({"path": path.name, "rawSha256": sha(raw), "reviewSha256": sha(clean)})
    output = {"baseline": BASE, "priorCandidate": PRIOR, "codeSha": git("rev-parse", "HEAD"),
              "branch": git("branch", "--show-current"), "integrationPerLatestMA": "5519630951f33417eacd57a8e0c2643d3016ea7f",
              "integrationNotMergedHere": True, "commands": commands,
              "protectedFiles": protected, "unchangedMethods": ast_checks, "resources": resource_checks,
              "originalEvidence": original_evidence,
              "protectedRefSnapshot": {ref: git("rev-parse", ref) for ref in ("main", "practical")},
              "relativeCodeDiffFromBaseline": git("diff", "--stat", BASE, "HEAD", "--", "packages", "tests"),
              "unverified": ["Linux/macOS execution", "three-platform remote CI", "whole repository suite",
                             "MA combined integration acceptance", "browser", "real providers", "real data migration", "deployment", "human acceptance"]}
    (OUT / "validation.json").write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / "artifact-whitespace.json").write_text(json.dumps(whitespace, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"backend": backend["counts"], "venv": venv["counts"], "resources": len(resource_checks), "codeSha": output["codeSha"]}))


if __name__ == "__main__":
    main()
