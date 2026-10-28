"""Fixture-only preservation and provider provenance regression, no server."""
import ast
import asyncio
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from tests.test_job_final_http_service import SessionProvider

OUT = Path(__file__).resolve().parent
BASE = "87c444ce896e768a75bc74fde1af834952f4875c"
path = "tests/test_job_final_http_service.py"
old = ast.parse(subprocess.check_output(["git", "show", BASE + ":" + path], text=True, encoding="utf-8"))
new = ast.parse(Path(path).read_text(encoding="utf-8"))
asserts = lambda t: [ast.dump(n, include_attributes=False) for n in ast.walk(t) if isinstance(n, ast.Assert)]
assert asserts(old) == asserts(new)
tests = lambda t: {n.name: ast.dump(n, include_attributes=False) for n in t.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")}
assert tests(old) == tests(new)


async def probe():
    provider = SessionProvider()
    key = {"runId": "owned-run", "targetId": "owned-target"}
    original = provider.send_session

    async def forward(*args, **kwargs):
        return await original(*args, **kwargs)

    provider.send_session = forward
    statuses = []
    for status in ("queued", "unknown", "rejected", "queued"):
        provider.unknown = status == "unknown"
        provider.fail_once = "text" if status == "rejected" else None
        reply = await provider.send_session("owned-test-session", "text", effect_key=key,
                                             source="agent", source_session_id="owned-source")
        assert reply["status"] == status
        statuses.append(reply["status"])
    assert provider.source_session_ids == ["owned-source"] * 4
    assert [effect for _, effect in provider.calls] == [key] * 4
    return {"forwardedStatusSequence": statuses, "sourceSessionIdsPreserved": True,
            "effectKeysPreserved": True}


checks = {}
for name in ("baseline", "fixture-regression"):
    file = OUT / (name + ".xml")
    tree = ET.parse(file)
    for suite in tree.getroot():
        suite.set("hostname", "windows-local")
    tree.write(file, encoding="utf-8", xml_declaration=True)
    checks[name] = {"suite": tree.getroot()[0].attrib, "exitCode": 1 if name == "baseline" else 0}
report = {"baseline": BASE, "codeSha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
          "platform": "win32", "python": "3.14.5", "originalAssertCount": len(asserts(old)),
          "originalTestFunctionCount": len(tests(old)), "assertAstEqual": True,
          "allOriginalTestFunctionsAstEqual": True, "providerProbe": asyncio.run(probe()),
          "checks": checks, "newRemoteRun": False}
(OUT / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report, indent=2))
