"""Read-only source provenance; this is not runtime or migration evidence."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

out = Path(__file__).resolve().parent
root = out.parents[1]
def git(*args):
    return subprocess.check_output(['git', *args], cwd=root)
records = []
for ref, path, names in [
    ('9b4506', 'packages/core/background_jobs.py', ['_redeliver_undelivered', '_backlog_undelivered_fire', 'recover_notifications', '_broadcast_independent_terminals']),
    ('68be5bd4d^', 'packages/core/background_jobs.py', ['_redeliver_undelivered', '_backlog_undelivered_fire']),
    ('HEAD', 'packages/jobs/pan_bridge.py', ['_recover_legacy_events']),
    ('HEAD', 'packages/jobs/independent_runner.py', ['run']),
    ('HEAD', 'packages/jobs/final_service.py', ['_admit_event', 'tick', 'deliver_outbox', '_settle']),
    ('HEAD', 'packages/jobs/final_runtime/ledger.py', ['ensure_outbox']),
]:
    sha = git('rev-parse', ref).decode().strip()
    raw = git('show', sha + ':' + path)
    text = raw.decode('utf-8-sig')
    lines = text.splitlines()
    functions = {node.name: node for node in ast.walk(ast.parse(text))
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in names:
        node = functions[name]
        records.append({'sourceSha': sha, 'path': path, 'symbol': name,
            'fileSha256': hashlib.sha256(raw).hexdigest(), 'start': node.lineno, 'end': node.end_lineno,
            'source': '\n'.join(lines[node.lineno - 1:node.end_lineno])})
(out / 'old-current-callchains.json').write_text(json.dumps({'claim': 'source provenance only',
    'records': records}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'sourceRecords': len(records), 'noRuntimeClaim': True}))
