"""Synthetic publication inventory only; no effect/provider is invoked."""
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
from packages.jobs.final_service import FinalService, object_ref, definition
from packages.web.server import _bind_qq_channel_provider

with tempfile.TemporaryDirectory(prefix='owned-r25-contract-') as tmp:
    owned = Path(tmp)
    service = FinalService(owned / 'canonical', CanonicalUserAuth(owned / 'auth'))
    try:
        _bind_qq_channel_provider(service)
        service._install_product_actions()
        actions = service.actions(actor={'type': 'user', 'id': 'local'})['items']
        releases = [{'ref': object_ref('action', a), 'definition': definition('action', a)}
            for a in actions if a['id'] in {'product_channel', 'product_channel_runtime_v1'}]
        assert len(releases) == 2
    finally:
        asyncio.run(service.close())
data = {'sourceSha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
    'layer': 'synthetic publication/schema inventory, not execution or migration proof',
    'releases': releases, 'oldPublicationUnchanged': True, 'tempRootRemoved': not owned.exists(),
    'interpreter': sys.executable, 'python': sys.version,
    'dependencies': {p: version(p) for p in ('mcp', 'pytest', 'nonebot2', 'nonebot-adapter-onebot', 'httpx')},
    'contract': {'owner': 'delivery-manager', 'actionPhase': 'before-provider-acceptance',
        'jobPhase': 'before-reservation', 'binding': 'existing input paths only',
        'currentFields': ['targetType', 'targetId', 'botUin', 'text'], 'nullBotUin': 'original omitted/default bot',
        'jobRuntime': {'owner': 'delivery-manager', 'targetMode': 'runtime',
            'inputFields': ['targetType', 'targetId', 'botUin', 'text'],
            'allowedStates': ['pending', 'reserved'], 'phase': 'before-reservation',
            'concurrency': 'job-config-cas+event-receipt', 'audit': 'final_operations', 'unknownPolicy': 'query-original'},
        'pendingFact': 'existing plan.runtimeDelivery[step].children[slot].backlog',
        'fields': ['dispatchKey', 'fireAt', 'text', 'error'],
        'api': 'GET /api/job-runs/{runId}: deliveryRuntime.steps[step].pendingFires plus original receipt',
        'identity': 'same original Run/Step/Attempt/effectKey; no automatic retry budget',
        'knownNoAttempt': 'original trusted phase and exact Attempt/effect/arguments; ordinary pulse only',
        'unknown': 'no remote query protocol; retain original identity, do not resend',
        'migration': 'new release opt-in must be mapped by migration owner; no real collection read or activation'}}
(Path(__file__).parent / 'contract-handoff.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'sourceSha': data['sourceSha'], 'refs': [r['ref'] for r in releases], 'tempRootRemoved': data['tempRootRemoved']}))
