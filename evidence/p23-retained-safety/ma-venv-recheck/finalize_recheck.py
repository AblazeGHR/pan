"""Summarize the exact interpreter recheck and verify prior artifacts unchanged."""
from collections import Counter
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
BEFORE = "260f24183b6a50b3c99288005b88fa7c9f5401a7"
CODE = "4d9ab1c8857f8caa4a3150c55a03fdb8e7f527d3"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    records = []
    for label in ("system", "pan-main-venv"):
        record = json.loads((OUT / (label + "-command.json")).read_text(encoding="utf-8"))
        assert record["exit"] == 0 and record["counts"] == {"passed": 26}
        for row in record["priorEvidence"]:
            assert sha((ROOT / row["path"]).read_bytes()) == row["sha256"], row["path"]
        group_counts = Counter(n["node"].split("::")[0] for n in record["nodes"])
        assert sorted(group_counts.values()) == [3, 3, 8, 12], group_counts
        record["fileCounts"] = dict(group_counts)
        for row in record["resources"]:
            proof = json.loads((OUT / row["path"]).read_text(encoding="utf-8"))
            assert proof["portReleased"] and proof["ownedProcessesRemaining"] == []
            assert proof.get("canonicalPortReleased", True) and proof.get("productProcessesRemaining", []) == []
            if proof.get("label") in ("canonical-delete-unknown-false", "canonical-delete-unknown-true"):
                assert proof["canonicalCleanupRequested"] is True
                assert proof["originalIdentityVerified"] and proof["identityKind"] == "filetime"
                assert proof["pidReuseStaleBirthRefused"]
                assert proof["cleanupCalls"] == proof["effects"] == 1
                if label == "pan-main-venv":
                    assert proof["launcherIsSupervisor"] is False
                    assert proof["ownershipRootIsScript"] is False
        records.append(record)
    # No product/test change since the previous delivered candidate; all old
    # evidence remains intact, including a700 red/fallback experiments.
    assert git("diff", "--name-only", BEFORE, "--", "packages", "tests", "evidence/p23-retained-safety/public-resume") == ""
    changed_source = git("diff", "--name-only", "bdd4c778", CODE, "--", "packages", "tests").splitlines()
    source = [{"path": p, "blob": git("rev-parse", CODE + ":" + p)} for p in changed_source]
    raw_checks = []
    with zipfile.ZipFile(OUT / "raw-output.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted([*OUT.glob("*.log"), *OUT.glob("*.xml")]):
            raw = path.read_bytes()
            archive.writestr(path.name, raw)
            clean = ("\n".join(line.rstrip() for line in raw.decode("utf-8").splitlines()).rstrip() + "\n").encode("utf-8")
            path.write_bytes(clean)
            raw_checks.append({"path": path.name, "rawSha256": sha(raw), "reviewSha256": sha(clean)})
    result = {"codeSha": CODE, "priorCleanSha": BEFORE, "baseline": "bdd4c778d55daff58529635b2adc464c03a1393c",
              "branch": git("branch", "--show-current"), "sourceUnchanged": True,
              "priorEvidenceUnchanged": True, "source": source, "commands": records,
              "protectedRefs": {ref: git("rev-parse", ref) for ref in ("main", "practical")},
              "rawArtifacts": raw_checks,
              "unverified": ["MA acceptance", "MA integration combination", "Linux/macOS", "three-platform remote CI",
                             "whole repository suite", "browser", "real provider", "real data migration", "deployment"]}
    (OUT / "validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sourceUnchanged": True, "priorEvidenceUnchanged": True,
                      "system": records[0]["counts"], "panMainVenv": records[1]["counts"]}))


if __name__ == "__main__":
    main()
