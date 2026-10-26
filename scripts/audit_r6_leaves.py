"""Reproducible, read-only R6-A symbol/import/manifest inventory.

Run before editing and after removal. Hits are evidence for review, not an
automatic reachability proof: same-named members and retired route bodies are
reported too. No source record, log content or runtime profile is opened.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess


REMOVED = {
    "packages/core/background_jobs.py": [
        "start", "retry", "start_scheduled_process", "_message_next_run",
        "_normalize_target_session_ids", "_message_job_common", "start_message",
        "start_broadcast", "update_message", "run_due_message_jobs",
        "_tail_log_output", "_run_job_action", "_execute_job_action",
        "_scheduled_task_requeue_after", "_append_task_run",
        "_reconcile_scheduled_processes", "_redeliver_undelivered",
        "_advance_paused_entries", "_repair_missing_next_fire",
        "_backlog_undelivered_fire", "_fire_scheduled_entry",
        "run_due_scheduled_tasks", "_terminal_broadcast_candidate",
        "_mark_terminal_broadcast", "_broadcast_independent_terminals",
        "recover_notifications", "_recovery_loop", "start_recovery_loop",
    ],
    "packages/jobs/system_actions.py": ["invoke", "execute"],
    "packages/scheduler/engine.py": [
        "_emit", "_emit_strict", "_consume", "_ensure_registered",
        "start_loop", "tick_once", "run_now",
    ],
}

REPLACED = {
    "packages/web/server.py": [
        "_legacy_action_job",
    ],
    "packages/jobs/api.py": ["create_job", "patch_job", "run_job_now"],
    "packages/scheduler/api.py": [
        "create_task", "update_task", "pause_task", "resume_task", "run_task_now",
    ],
}


def production_references(root, source_overrides=None):
    """Resolve module/import aliases, internal names and literal getattr calls.

    This complements lexical hits (which include documentation/old fixtures).
    Dynamic nonliteral loader arguments remain a manual audit responsibility.
    """
    targets = {path.removesuffix(".py").replace("/", ".") + "." + name
               for path, names in REMOVED.items() for name in names}
    found = []
    for path in sorted((root / "packages").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if "/e2e/" in relative or "node_modules" in path.parts or "__pycache__" in path.parts:
            continue
        module = relative.removesuffix(".py").replace("/", ".")
        package = module.rpartition(".")[0]
        if module.endswith(".__init__"):
            module = package
        source = (source_overrides or {}).get(relative)
        tree = ast.parse(source if source is not None else path.read_text(encoding="utf-8-sig"))
        aliases = {}
        owners = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for line in range(node.lineno, node.end_lineno + 1):
                    owners[line] = node.name
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                base = importlib.util.resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module
                for item in node.names:
                    aliases[item.asname or item.name] = base + "." + item.name

        def qualified(node):
            if isinstance(node, ast.Name):
                return aliases.get(node.id, module + "." + node.id)
            if isinstance(node, ast.Attribute):
                return qualified(node.value) + "." + node.attr
            return ""

        for node in ast.walk(tree):
            target = qualified(node)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                target = qualified(node.args[0]) + "." + node.args[1].value
            if target in targets:
                found.append({"path": relative, "line": node.lineno, "owner": owners.get(node.lineno), "target": target})
            if isinstance(node, ast.ImportFrom):
                for item in node.names:
                    target = aliases.get(item.asname or item.name)
                    if target in targets:
                        found.append({"path": relative, "line": node.lineno, "owner": owners.get(node.lineno), "target": target})
    return found


def scan(root):
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode("utf-8").split("\0")
    needles = set(name for names in REMOVED.values() for name in names)
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(needles)) + r")\b")
    refs = {name: [] for name in needles}
    imports, manifests, files, definitions = [], [], [], {}
    for relative in paths:
        path = root / relative
        if relative.startswith("evidence/r6-a-20261026/"):
            continue  # Do not recursively scan this audit's own generated output.
        if path.suffix not in {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".toml", ".yml", ".yaml", ".ini", ".cfg"} or not path.is_file():
            continue
        raw = path.read_bytes()
        try:
            source = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
        files.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
        owners = {}
        if path.suffix == ".py":
            try:
                tree = ast.parse(source)
            except SyntaxError:
                tree = None
            if tree:
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        for line in range(node.lineno, node.end_lineno + 1):
                            owners[line] = node.name
                    if isinstance(node, ast.ImportFrom) and node.module and any(s in node.module for s in ("background_jobs", "system_actions", "scheduler", "action_runtime", "automation")):
                        imports.append({"path": relative, "line": node.lineno, "module": node.module,
                                        "names": [a.name for a in node.names]})
                    if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id in {"__import__", "getattr"}) or
                            (isinstance(node.func, ast.Attribute) and node.func.attr in {"import_module", "add_api_route", "include_router"})):
                        imports.append({"path": relative, "line": node.lineno, "dynamicOrRoute": ast.unparse(node)[:220]})
                if relative in REMOVED:
                    definitions[relative] = {n.name: {"line": n.lineno, "endLine": n.end_lineno}
                                             for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                             and n.name in REMOVED[relative]}
        for line, value in enumerate(source.splitlines(), 1):
            for name in set(pattern.findall(value)):
                refs[name].append({"path": relative, "line": line, "owner": owners.get(line),
                                   "kind": "test" if relative.startswith("tests/") else
                                   "production" if relative.startswith("packages/") and "/e2e/" not in relative else "artifact-or-tool"})
            if any(x in value for x in ("background_jobs", "system_actions", "scheduler.engine")) and path.suffix != ".py":
                manifests.append({"path": relative, "line": line})
    return {"schema": "pan.r6-a.reverse-references/v1", "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "removed": REMOVED, "replacedBodies": REPLACED, "definitions": definitions,
            "files": files, "references": refs, "dynamicImportsAndRoutes": imports, "nonPythonManifestHits": manifests,
            "qualifiedProductionReferences": production_references(root),
            "limits": "Lexical whole tracked repository scan, AST owners/imports/routes. Same-name hits require manual resolution; effective routes are checked by tests. No claim about external plugins or deployed processes."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = scan(Path(__file__).resolve().parents[1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(result["files"]), "symbols": sum(map(len, REMOVED.values())),
                      "definitions": sum(map(len, result["definitions"].values())), "output": str(args.output)}))
