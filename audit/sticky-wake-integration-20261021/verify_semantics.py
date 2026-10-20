"""Verify preserved main behavior and incoming queue/Force helpers without imports."""
import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
MAIN = "20473b41aeeb8dec9aadf4dac1bfe7368ca3f737"
INCOMING = "e160782e636d828427e9262e952a1f25c09117b9"


def src(ref, path):
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT).decode("utf-8-sig")


def body(source, name):
    node = next(n for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(source, node)


checks = []
for path, names, ref in [
    ("packages/core/worker.py", ["_read_stdout"], MAIN),
    ("packages/core/worker.py", ["_commit_offline", "_stage_new_queue_item", "_append_new_queue_item", "_persist_task_item", "wake_session", "send_session", "assign", "_queue_recovery_blocked", "_ensure_worker", "restart_or_start_worker", "clear_session_illegal_for_startup"], INCOMING),
    ("packages/web/server.py", ["api_sessions_wake", "api_put_settings_session_lifecycle"], INCOMING),
]:
    baseline = src(ref, path); current = (ROOT / path).read_text(encoding="utf-8")
    for name in names:
        assert body(baseline, name) == body(current, name), (path, name)
        checks.append({"path": path, "function": name, "from": ref, "identical": True})
for path in ["packages/core/session.py", "packages/core/workdir_retention.py", "packages/core/workspace.py", "packages/core/data_retention.py", "packages/mcp/server.py", "packages/web/src/components/chat/InputRow.tsx", "packages/web/src/components/chat/ServerAttachmentPicker.tsx", "packages/web/src/components/session/HandoffSessionModal.tsx", "packages/web/src/components/layout/DataSettingsPanel.tsx"]:
    baseline = src(MAIN, path).replace("\r\n", "\n"); current = (ROOT / path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    assert baseline == current, path
    checks.append({"path": path, "from": MAIN, "identical": True, "sha256": hashlib.sha256(current.encode()).hexdigest()})
for path, prefix in [("docs/skills/pan/SKILL.md", "**按消息用途选择工具**"), ("docs/skills/pan/SKILL.md", "4. **B.original_prompt"), ("docs/skills/pan/references/http-api.md", "| `POST` | `/api/sessions/{id}/handoff`")]:
    line = next(line for line in src(MAIN, path).splitlines() if line.startswith(prefix))
    assert line in (ROOT / path).read_text(encoding="utf-8"), prefix
    checks.append({"path": path, "preservedMainParagraph": line, "identical": True})
(OUT / "semantic-preservation.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
print("Semantic preservation assertions:", len(checks))
