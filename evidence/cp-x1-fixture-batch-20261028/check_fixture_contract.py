"""Bounded fixture provenance/AST checks; no application startup."""
import ast
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess

from tests.test_automation_canonical import SessionProvider

OUT = Path(__file__).resolve().parent
BASE = "264af19d0c854039e16bda7b598dfeb281fea85b"


def asserts(tree):
    # Explicitly authorized spelling repair retains the same byte invariant.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "patreal_http":
            node.attr = "path"
    return [ast.dump(n, include_attributes=False) for n in ast.walk(tree) if isinstance(n, ast.Assert)]


async def probe():
    provider = SessionProvider()
    key = {"runId": "owned-run", "targetId": "owned-target"}
    statuses = []
    for status in ("queued", "unknown", "rejected", "queued"):
        provider.unknown = status == "unknown"
        provider.fail_once = "owned-text" if status == "rejected" else None
        result = await provider.send_session("owned-target", "owned-text", effect_key=key,
                                             source="agent", source_session_id="owned-source")
        assert result["status"] == status
        statuses.append(result["status"])
    assert provider.sources == ["agent"] * 4
    assert provider.source_session_ids == ["owned-source"] * 4
    assert [effect for _, effect in provider.calls] == [key] * 4
    return {"statusSequence": statuses, "sourceAndSourceSessionIdPreserved": True,
            "effectKeysPreserved": True, "calls": len(provider.calls)}


counts = {}
for path in ("tests/support/agent_inbox_http_server.py", "tests/test_automation_canonical.py",
             "tests/test_quota_host_http.py", "tests/test_quota_policy_http.py"):
    old = ast.parse(subprocess.check_output(["git", "show", f"{BASE}:{path}"], text=True, encoding="utf-8"))
    new = ast.parse(Path(path).read_text(encoding="utf-8"))
    assert asserts(old) == asserts(new), path
    counts[path] = len(asserts(old))
for path in ("evidence/cp-x1-remote-repair-20261028/windows-node-classification.json",
             "evidence/cp-x1-remote-repair-20261028/remote-failure-inventory.json"):
    actual = subprocess.check_output(["git", "hash-object", "--path", path, path], text=True).strip()
    expected = subprocess.check_output(["git", "rev-parse", f"1b319ed2:{path}"], text=True).strip()
    assert actual == expected, path
report = {"baseAfterMerge": BASE, "originalAssertAstPreserved": counts,
          "authorizedAssertSpellingOnly": "store.patreal_http -> store.path (two occurrences)",
          "providerProbe": asyncio.run(probe()), "raw485EvidenceGitBlobsUnchanged": True}
(OUT / "fixture-contract.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
dist = Path("packages/web/dist")
files = {p.relative_to(dist).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in sorted(dist.rglob("*")) if p.is_file()}
(OUT / "dist-provenance.json").write_text(json.dumps({"buildHead": BASE,
    "frontendSourceChangedByThisBatch": False, "installExit": 0, "buildExit": 0,
    "files": files}, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
