"""Reproduce the original 49-skip audit and copy only relevant user authorization."""
import ast
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
ORIGINAL = "e160782e636d828427e9262e952a1f25c09117b9"
REASON = "Oneshot mode retired by user; implementation retained"


def functions(source):
    tree = ast.parse(source)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_"):
            decorators = [ast.get_source_segment(source, d) or "" for d in n.decorator_list]
            yield n, any(REASON in d for d in decorators)


split = {
    "test_native_adapter_keeps_legacy_and_first_spawn_separate": "test_retired_oneshot_cbc_capability_declaration",
    "test_execution_modes_declared": "test_retired_oneshot_cbc_capability_declaration",
    "test_adapter_metadata": "test_retired_oneshot_claude_capability_declaration",
    "test_stream_and_control_protocol": "test_retired_oneshot_excludes_stream_input_protocol",
    "test_worker_and_oneshot_adapters_consume_effective_prompt": "test_retired_oneshot_adapters_consume_effective_prompt",
}
restored = {
    "test_long_system_prompt_never_selects_oneshot_for_cbc_or_claude": "Current historical-mode-to-stream fallback; long prompt protection remains supported.",
    "test_usage_enrichment_rollback_does_not_double_count": "False positive: only the fixture Session ID says oneshot; actual code tests shared usage enrichment rollback, not the retired consumer.",
}


def explanation(path, name):
    if name in split: return "Mixed test: restore current stream/native/prompt assertions and isolate the old Oneshot fragment."
    if name in restored: return restored[name]
    if "oneshot_args" in path: return "CBC per-task Oneshot argv construction, MCP, permissions, resume or prompt positioning."
    if "cli_session_binding" in path: return "Direct _consumer_oneshot CLI session binding/error collection."
    if "oneshot_usage" in path: return "Direct Oneshot consumer terminal/usage enrichment. Shared rollback test is restored separately."
    if "worker_output_mode" in path: return "Legacy explicit Oneshot selection or one-shot-only adapter selection/validation; current retirement boundary remains tested."
    if "worker_watchdog" in path or "global_watchdog" in path: return "Retired process=None Oneshot lifecycle/reclamation, not a stream or Job one-shot timer."
    if "report_error_context" in path: return "Runs _run_oneshot_worker, hence the retired consumer; active stream/report tests remain enabled."
    if "task_id_inheritance" in path: return "Forces resolve_execution_mode to oneshot and substitutes _consumer_oneshot; other task identity tests stay active."
    if "terminal_broadcast" in path: return "Direct Oneshot consumer terminal/result/Idle pair; stream sibling remains active."
    if "unified_server_queue" in path: return "Direct per-task Oneshot spawn and argv handoff, or its spawn-failure requeue."
    if "addressing_compat" in path: return "Retired process=None Oneshot worker registration or Force restart of this mode."
    if "claude_adapter" in path: return "Claude Oneshot argv or Oneshot stdout error extraction; current metadata/stream protocol restored separately."
    if "session_error_log_e2e" in path: return "Creates an outputMode=oneshot Session and invokes the retired mode."
    raise AssertionError((path, name))


archive = subprocess.check_output(["git", "archive", ORIGINAL, "tests", "packages/web/src"], cwd=ROOT)
rows = []
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    for entry in tar.getmembers():
        if not entry.isfile(): continue
        source = tar.extractfile(entry).read().decode("utf-8-sig")
        if entry.name.endswith(".py") and REASON in source:
            for node, skipped in functions(source):
                if not skipped: continue
                code = ast.get_source_segment(source, node)
                rows.append({"kind": "python", "originalFile": entry.name, "originalLine": node.lineno,
                    "test": node.name, "originalBodySha256": hashlib.sha256(code.encode()).hexdigest(),
                    "evidence": [line.strip() for line in code.splitlines() if any(t in line.lower() for t in ("oneshot", "one-shot", "one_shot", "process=none", "process = none"))][:8],
                    "verdict": "split" if node.name in split else "restore" if node.name in restored else "retain-retired-skip",
                    "reason": explanation(entry.name, node.name), "legacyReplacement": split.get(node.name)})
        if entry.name.endswith((".test.ts", ".test.tsx")):
            for match in re.finditer(r"it\.skip\('([^']+)'", source):
                assert entry.name in {"packages/web/src/components/chat/InputRow.test.tsx", "packages/web/src/components/session/NewSessionModal.test.tsx", "packages/web/src/utils/quickNewSession.test.ts"}, entry.name
                rows.append({"kind": "frontend", "originalFile": entry.name,
                    "originalLine": source[:match.start()].count("\n") + 1, "test": match[1],
                    "verdict": "retain-retired-skip", "reason": "Legacy Oneshot selection/Steer visibility or restoring saved Oneshot UI/defaults. Current stream/native and historical-mode fallback counterparts stay active; InputRow skipped body narrowed to only Oneshot."})
assert len(rows) == 49, len(rows)
current = []
for p in (ROOT / "tests").glob("test_*.py"):
    source = p.read_text(encoding="utf-8-sig")
    if REASON not in source: continue
    for node, skipped in functions(source):
        if skipped: current.append({"file": p.relative_to(ROOT).as_posix(), "line": node.lineno, "test": node.name})
assert len(current) == 43, len(current)
(OUT / "skip-audit.json").write_text(json.dumps({"originalCommit": ORIGINAL, "originalCount": 49,
    "originalRows": rows, "currentPythonSkips": current, "currentFrontendSkips": 3,
    "currentTotal": 46, "restoredCurrentCases": sorted([*split, *restored])}, ensure_ascii=False, indent=2), encoding="utf-8")

record = Path("C:/Users/14709/.codex/sessions/2026/10/18/rollout-2026-10-18T03-19-37-01a14b4e-2d9b-7d41-9fe9-7fb7b80b0735.jsonl")
authority = []
for line_number, line in enumerate(record.open(encoding="utf-8"), 1):
    event = json.loads(line); payload = event.get("payload", {})
    if event.get("type") != "response_item" or payload.get("role") != "user": continue
    body = "\n".join(c.get("text", "") for c in payload.get("content", []) if isinstance(c, dict))
    if not (body.startswith("1.对 2. Oneshot现在") or body.startswith("机制改动，新机制下")): continue
    authority.append({"source": str(record).replace("\\", "/"), "line": line_number,
        "timestamp": event.get("timestamp"), "messageId": payload.get("id"),
        "turnId": payload.get("internal_chat_message_metadata_passthrough", {}).get("turn_id"),
        "text": body, "textSha256": hashlib.sha256(body.encode()).hexdigest()})
assert len(authority) == 2, len(authority)
(OUT / "authorization.json").write_text(json.dumps(authority, ensure_ascii=False, indent=2), encoding="utf-8")
lines = ["# Original 49 skip audit", "", "Original blob: `" + ORIGINAL + "`. Seven current/shared cases are restored; five legacy fragments are narrowed into four skipped tests (CBC declaration is shared). Current total: **43 Python + 3 frontend = 46 skips**. Original usage rollback skip was a false positive, not authorized retirement of shared usage accounting.", "", "Authorization: [original user records](authorization.json). No unrelated Job one-shot scheduling tests are skipped.", "", "| # | Original test and location | Verdict | Reason / preserved current coverage |", "| --- | --- | --- | --- |"]
for index, row in enumerate(rows, 1):
    lines.append(f"| {index} | `{row['originalFile']}:{row['originalLine']} {row['test']}` | {row['verdict']} | {row['reason']}" + (f" Legacy-only: `{row['legacyReplacement']}`." if row.get('legacyReplacement') else "") + " |")
(OUT / "skip-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({"audited": len(rows), "restoredCurrent": len(split) + len(restored), "currentPythonSkip": len(current), "frontendSkip": 3, "authorization": [(r['line'], r['timestamp']) for r in authority]}))
