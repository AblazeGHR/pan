"""Synthetic publication/schema inventory; explicitly not execution proof."""
import asyncio
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_service import FinalService, definition, object_ref

with tempfile.TemporaryDirectory(prefix='pan-retire-contract-') as tmp:
    owned = Path(tmp)
    service = FinalService(owned / 'canonical', CanonicalUserAuth(owned / 'auth'))
    try:
        actions = service.actions(actor={'type': 'user', 'id': 'local'})['items']
        releases = [{'reference': object_ref('action', a), 'definition': definition('action', a)}
                    for a in actions if a['id'].endswith('_runtime_v1')]
        assert len(releases) == 3
    finally:
        asyncio.run(service.close())
data = {'sourceSha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'layer': 'synthetic publication/schema inventory only; no Run/effect/provider invocation',
    'interpreter': sys.executable, 'python': sys.version, 'dependencies': {p: version(p) for p in ('mcp', 'tzlocal', 'pytest')},
    'newImmutableReleases': releases, 'temporaryRootCleaned': not owned.exists(),
    'schemaArtifacts': ['common.schema.json', 'action.schema.json', 'action_definition.schema.json',
                        'job.schema.json', 'job_definition.schema.json', 'run.schema.json'],
    'oldV2Mutation': False, 'realMigrationOrActivation': False}
(Path(__file__).resolve().parent / 'contract-handoff.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'sourceSha': data['sourceSha'], 'releases': len(releases), 'temporaryRootCleaned': data['temporaryRootCleaned']}))
