"""Evidence only: exact final commands, original red experiments and fences."""
from collections import Counter
from pathlib import Path
import ast
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = "67d125de794132f72011012d8c6814110aafe8ef"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def members(source, cls=None):
    tree = ast.parse(source)
    if cls:
        tree = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {n.name: ast.dump(n, include_attributes=False) for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def main():
    commands = []
    for path in sorted(OUT.glob("*-command.json")):
        entry = json.loads(path.read_text(encoding="utf-8"))
        xml = OUT / (path.name.removesuffix("-command.json") + ".xml")
        if xml.exists():
            nodes = []
            for case in ET.parse(xml).getroot().findall(".//testcase"):
                status = next((s for s in ("failure", "error", "skipped") if case.find(s) is not None), "passed")
                detail = case.find(status)
                nodes.append({"node": case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"],
                              "status": status, **({"message": detail.attrib.get("message", "")} if detail is not None else {})})
            entry.update(counts=dict(Counter(n["status"] for n in nodes)), nodes=nodes)
        entry["label"] = path.name.removesuffix("-command.json")
        commands.append(entry)
    final = [c for c in commands if c["label"] in {"system-final-retry", "venv-final", "import-final"}]
    assert len(final) == 3 and all(c["exit"] == 0 for c in final)
    assert next(c for c in final if c["label"] == "venv-final")["counts"] == {"passed": 46}
    preserved_methods = []
    for path, cls in (("packages/jobs/final_service.py", "FinalService"), ("packages/jobs/final_api.py", None)):
        old = members(git("show", BASE + ":" + path), cls)
        new = members((ROOT / path).read_text(encoding="utf-8"), cls)
        assert all(new[name] == value for name, value in old.items()), path
        preserved_methods.append({"path": path, "unchangedExistingMethods": sorted(old),
                                  "addedOnly": sorted(set(new) - set(old))})
    protected = []
    paths = ["packages/actions/typed_executor.py", "packages/core/platform/execution_host.py",
             "packages/jobs/final_runtime/coordinator.py", "packages/jobs/final_runtime/ledger.py",
             "packages/core/background_jobs.py", "tests/conftest.py"]
    paths += ["tests/test_job_retained_safety_" + n + ".py" for n in ("owned", "canonical", "protocol", "public")]
    paths += git("ls-tree", "-r", "--name-only", BASE, "packages/scheduler", "packages/web/src").splitlines()
    for path in paths:
        original = git("rev-parse", BASE + ":" + path)
        current = git("hash-object", "--path=" + path, path)
        assert current == original, path
        protected.append({"path": path, "blob": original, "unchanged": True})
    old_evidence = git("ls-tree", "-r", "--name-only", BASE, "evidence/p23-retained-safety", "evidence/r6i-repair").splitlines()
    assert git("diff", "--name-only", BASE, "--", "evidence/p23-retained-safety", "evidence/r6i-repair") == ""
    resources = []
    for axis in ("system", "venv", "safety-system", "safety-venv"):
        for path in sorted((OUT / axis).glob("*.json")):
            proof = json.loads(path.read_text(encoding="utf-8"))
            assert proof["portReleased"]
            assert proof.get("ownedProcessesRemaining", []) == [] and proof.get("supervisorsRemaining", []) == []
            assert proof.get("productProcessesRemaining", []) == [] and proof.get("canonicalPortReleased", True)
            resources.append({"path": path.relative_to(OUT).as_posix(), "cleanupVerified": True})
    experiments = []
    for name in ("compat-first", "first", "second", "third"):
        suite = ET.parse(OUT / (name + ".xml")).getroot().find("testsuite")
        experiments.append({"label": name, **{k: int(suite.attrib[k]) for k in ("tests", "failures", "errors", "skipped")}})
    raw = []
    with zipfile.ZipFile(OUT / "raw-output.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted([*OUT.glob("*.log"), *OUT.glob("*.xml")]):
            data = path.read_bytes()
            archive.writestr(path.name, data)
            try:
                clean = ("\n".join(line.rstrip() for line in data.decode("utf-8").splitlines()).rstrip() + "\n").encode("utf-8")
            except UnicodeDecodeError:
                # A real process may emit arbitrary bytes. Keep the review copy
                # byte-identical; raw-output.zip already preserves it as well.
                clean = data
            path.write_bytes(clean)
            raw.append({"path": path.name, "rawSha256": sha(data), "reviewSha256": sha(clean)})
    result = {"base": BASE, "codeSha": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
              "commands": commands, "experiments": experiments, "resources": resources,
              "preservedMethods": preserved_methods, "protectedFiles": protected,
              "priorEvidenceUnchangedCount": len(old_evidence), "artifacts": raw,
              "protectedRefs": {r: git("rev-parse", r) for r in ("main", "practical")},
              "unverified": ["MA combined publication/integration acceptance", "Linux/macOS execution",
                             "three-platform remote CI", "whole repository suite", "browser", "real data migration", "deployment"]}
    (OUT / "validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"commands": [{"label": c["label"], "exit": c["exit"], "counts": c.get("counts")} for c in commands],
                      "resources": len(resources), "codeSha": result["codeSha"]}))


if __name__ == "__main__":
    main()
