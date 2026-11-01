"""Repository-only retirement inventory. Exit 2 means deletion remains blocked.

This command has no profile argument, owner-adoption or execution operation.
It inventories tracked source, including packaging/manifests, without importing
the product. Runtime behavior must be supplied by separately executed tests.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess

BASELINE = 'add4bef7773216dd79793733a50a2ec19a6869c3'
HOSTS = {'packages.scheduler.' + name for name in ('store', 'time_events', 'legacy_bridge')}
TARGETS = HOSTS | {'packages.core.background_jobs'} | {
    'packages.jobs.' + name for name in
    ('action_runtime', 'legacy_compat', 'pan_bridge', 'pins', 'scheduled_records', 'independent_runner')}
REQUIRED = ('main.py', 'manifest.json', 'packages/mcp/manifest.json',
    'packages/web/server.py', 'packages/jobs/final_service.py',
    'docs/design/job-unification/R5_R6_EXECUTION_PLAN_20261026.md')


def imports(tree, relative):
    """Resolve from-package imports, including relative `from . import host`."""
    package = list(Path(relative).parent.parts)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = '.'.join(package[:len(package) - node.level + 1]) if node.level else ''
            module = '.'.join(part for part in (base, node.module) if part)
            names = [module] + [module + '.' + alias.name for alias in node.names]
        for name in sorted(TARGETS.intersection(names)):
            yield {'path': relative, 'line': node.lineno, 'module': name}


def scan(root, tracked):
    edges, dynamic, errors, surfaces, files = [], [], [], [], []
    for relative in sorted(set(tracked)):
        # Deliberately exclude every data/profile/evidence directory, even if tracked.
        if not (relative.startswith(('packages/', 'scripts/')) or relative in REQUIRED
                or relative in ('setup.py', 'setup.cfg', 'pyproject.toml', 'MANIFEST.in')):
            continue
        path = root / relative
        if not path.is_file() or path.suffix not in {
                '.py', '.json', '.ts', '.tsx', '.js', '.mjs', '.toml', '.cfg', '.in', '.bat', '.sh'}:
            continue
        raw = path.read_bytes()
        try:
            source = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            errors.append({'path': relative, 'error': 'non-UTF8 source requires review'})
            continue
        files.append({'path': relative, 'sha256': hashlib.sha256(raw).hexdigest()})
        if path.suffix == '.py':
            try:
                tree = ast.parse(source)
            except SyntaxError as exc:
                errors.append({'path': relative, 'line': exc.lineno, 'error': str(exc)})
                continue
            edges.extend(imports(tree, relative))
            dynamic_aliases = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in ('importlib', 'importlib.util', 'builtins'):
                    dynamic_aliases.update(alias.asname or alias.name for alias in node.names
                        if alias.name in {'import_module', '__import__', 'find_spec',
                                          'spec_from_file_location', 'exec_module'})
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = ast.unparse(node.func)
                    if name in dynamic_aliases or name.endswith(('import_module', '__import__', 'find_spec',
                                      'spec_from_file_location', 'exec_module')):
                        dynamic.append({'path': relative, 'line': node.lineno,
                            'code': ast.unparse(node), 'status': 'requires-review'})
        for line, value in enumerate(source.splitlines(), 1):
            if re.search(r'legacy|background.jobs|scheduler[./]|/api/jobs|/executions|final_tools', value, re.I):
                if (path.suffix != '.py' or relative in ('main.py', 'packages/web/server.py')):
                    surfaces.append({'path': relative, 'line': line,
                        'matches': sorted(set(re.findall(
                            r'legacy\w*|background.jobs|scheduler[./][\w/-]*|/api/jobs[\w/{}-]*|/executions[\w/{}-]*|final_tools',
                            value, re.I)))})
    return {'files': files, 'dependencyEdges': edges, 'dynamicCandidates': dynamic,
            'surfaceCandidates': surfaces, 'errors': errors,
            'missingRequired': [p for p in REQUIRED if not (root / p).is_file()],
            'retainedHosts': sorted(m for m in HOSTS if (root / (m.replace('.', '/') + '.py')).is_file())}


def decision(inventory, mapping):
    blockers = ['G2: no authorized all-cohort identity/owner/resource settlement evidence',
                'G3: no authorized real migration/classification acceptance evidence',
                'G4/G6: full management, notification transport, three-platform and user acceptance absent']
    if inventory['retainedHosts']:
        blockers.append('Three scheduler hosts are intentionally retained; deletion is not authorized')
    if inventory['dependencyEdges']:
        blockers.append('Production/offline dependency edges remain; inspect each before deleting its host')
    if inventory['dynamicCandidates']:
        blockers.append('Dynamic candidates require semantic review; lexical inventory is not execution proof')
    if inventory['missingRequired'] or inventory['errors'] or not inventory['files']:
        blockers.append('Incomplete repository inventory')
    gaps = [r['node'] for r in mapping['originalNodes'] if r['status'] != 'strict-successor']
    if gaps:
        blockers.append(f'{len(gaps)} original nodes lack accepted exact successors')
    return {'removalAllowed': False, 'migrationComplete': False, 'exitCode': 2,
            'blockers': blockers, 'originalNodeGaps': gaps,
            'limits': 'Repository observations only; no profile/owner counts inferred, no unknown retry.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    tracked = subprocess.check_output(['git', 'ls-files', '--cached', '--others',
        '--exclude-standard', '-z'], cwd=root).decode().split('\0')
    mapping = json.loads((root / 'evidence/job-retirement-closure/successors.json').read_text(encoding='utf-8'))
    inventory = scan(root, tracked)
    result = {'schema': 'pan.job-retirement-closure/v2', 'baseline': BASELINE,
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'inventory': inventory, 'decision': decision(inventory, mapping)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'exit': 2, 'files': len(inventory['files']),
        'edges': len(inventory['dependencyEdges']), 'removalAllowed': False}))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
