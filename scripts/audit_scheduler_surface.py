"""Read-only R6-C/D reverse references and import-only route/MCP catalog snapshot.

Scans every tracked and unignored text file, including tests, agents, scripts
and manifests.
References are candidates for review, not a claim about external deployments.
No lifespan, socket listener or application data store is opened.
"""
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess


PATTERN = re.compile(
    r'scheduler_(?:create|list|get|update|delete|pause|resume|run_now|runs)\b'
    r'|(?:/api/|\$\{BASE\}/|[\'"`])scheduler/(?:tasks|next|status|event-sources)'
    r'|scheduler[./]api|scheduler_api|owner-switch'
    r'|scheduler[./](?:store|engine|time_events|legacy_bridge|cron)\b'
    r'|scheduleStore|EventRegister|automationWorkspace|AutomationView'
    r'|(?:fetchScheduledTasks|createScheduledTask|updateScheduledTask|deleteScheduledTask'
    r'|pauseScheduledTask|resumeScheduledTask|runScheduledTaskNow|fetchTaskRuns'
    r'|fetchSchedulerNext|fetchSchedulerStatus|useScheduleStore)\b')


def effective_routes(app):
    for route in app.routes:
        if hasattr(route, 'effective_route_contexts'):
            yield from route.effective_route_contexts()
        else:
            yield route


def snapshot():
    from packages.web.server import app
    from packages.mcp.server import mcp
    routes = [{'path': r.path, 'methods': sorted(r.methods),
               'handler': r.endpoint.__module__ + '.' + r.endpoint.__name__,
               'deprecated': bool(r.deprecated)}
              for r in effective_routes(app)
              if getattr(r, 'path', '').startswith('/api/scheduler/')]
    return {'routes': routes, 'tools': sorted(t.name for t in mcp._tool_manager.list_tools())}


def scan(root):
    tracked = subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root).decode().split('\0')
    references, imports, dynamic, frontend_dynamic, scanned = [], [], [], [], []
    host_imports = []
    parse_errors = []
    hosts = {'packages.scheduler.' + name for name in
             ('store', 'engine', 'time_events', 'legacy_bridge', 'cron')}
    for relative in tracked:
        if not relative or relative.startswith(('evidence/r6c-scheduler-surface/',
                                                'evidence/r6d-scheduler-host/',
                                                'evidence/r6g-engine-cron/')):
            continue
        path = root / relative
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if b'\0' in raw:
            continue
        try:
            source = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            continue
        scanned.append(relative)
        for line, value in enumerate(source.splitlines(), 1):
            if PATTERN.search(value):
                references.append({'path': relative, 'line': line,
                                   'matches': sorted(set(PATTERN.findall(value)))})
            if path.suffix in {'.ts', '.tsx', '.js', '.mjs', '.cjs'}:
                for match in re.finditer(r'\b(?:import|require)\s*\([^\n)]+\)', value):
                    frontend_dynamic.append({'path': relative, 'line': line, 'code': match.group()})
        if path.suffix != '.py':
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            parse_errors.append({'path': relative, 'line': exc.lineno, 'message': exc.msg})
            continue
        for node in ast.walk(tree):
            resolved = []
            if isinstance(node, ast.Import):
                resolved = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                parts = list(Path(relative).parent.parts)
                base = '.'.join(parts[:len(parts) - node.level + 1]) if node.level else ''
                module = '.'.join(part for part in (base, node.module) if part)
                resolved = [module] + [module + '.' + alias.name for alias in node.names]
            for module in sorted(hosts.intersection(resolved)):
                host_imports.append({'path': relative, 'line': node.lineno,
                                     'module': module, 'code': ast.unparse(node)})
            if isinstance(node, (ast.Import, ast.ImportFrom)) and 'scheduler' in ast.unparse(node):
                imports.append({'path': relative, 'line': node.lineno, 'code': ast.unparse(node)})
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == '__import__'
                or isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'
            ):
                dynamic.append({'path': relative, 'line': node.lineno, 'code': ast.unparse(node)})
    return {'schema': 'pan.r6d.scheduler-host/v1',
            'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
            'scannedTextFiles': scanned, 'references': references,
            'schedulerImports': imports, 'allDynamicImports': dynamic,
            'hostImports': host_imports,
            'pythonParseErrors': parse_errors,
            'frontendDynamicImports': frontend_dynamic,
            'limits': 'Tracked/unignored repository text, Python AST and lexical JS/TS imports; '
                      'dynamic imports require review. '
                      'No proof about external plugins, deployed agents or real data.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--snapshot', action='store_true')
    args = parser.parse_args()
    result = snapshot() if args.snapshot else scan(Path(__file__).resolve().parents[1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output), **{
        key: len(value) for key, value in result.items() if isinstance(value, list)}}))
