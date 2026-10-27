"""Check production imports without importing or starting either legacy host."""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
from pathlib import Path

RETIRED = {"packages.scheduler.store", "packages.scheduler.time_events"}


def scan(root):
    static, dynamic, errors = [], [], []
    files = sorted((root / "packages").rglob("*.py"))
    for path in files:
        source = path.read_text(encoding="utf-8-sig")
        relative = path.relative_to(root).as_posix()
        package = ".".join(path.relative_to(root).parts[:-1])
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            errors.append({"path": relative, "line": exc.lineno, "message": exc.msg})
            continue
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = importlib.util.resolve_name("." * node.level + module, package)
                modules = [module, *(module + "." + alias.name for alias in node.names)]
            for module in sorted(set(modules) & RETIRED):
                static.append({"path": relative, "line": node.lineno, "module": module})
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                if name not in {"import_module", "__import__", "find_spec", "spec_from_file_location", "exec_module"}:
                    continue
                literal = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
                dynamic.append({"path": relative, "line": node.lineno,
                                "literal": literal if isinstance(literal, str) else None,
                                "code": ast.get_source_segment(source, node)})
    reviewed = {("packages/core/adapters/opencode/sessions.py", "time"),
                ("packages/jobs/independent_runner.py", "signal")}
    unreviewed = [row for row in dynamic if "/e2e/" not in row["path"]
                  and (row["path"], row["literal"]) not in reviewed]
    return {"files": len(files), "staticLegacyHostImports": static, "dynamicCandidates": dynamic,
            "literalDynamicLegacyHostImports": [row for row in dynamic if row["literal"] in RETIRED],
            "unreviewedProductionDynamicImports": unreviewed,
            "pythonParseErrors": errors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = scan(Path(__file__).resolve().parents[1])
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: len(value) if isinstance(value, list) else value for key, value in result.items()}))
    raise SystemExit(bool(result["staticLegacyHostImports"] or result["literalDynamicLegacyHostImports"]
                          or result["unreviewedProductionDynamicImports"] or result["pythonParseErrors"]))
