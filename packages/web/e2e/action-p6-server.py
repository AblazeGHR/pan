"""Owned cooperative old Group -> adopted serial successor for production UI."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import uvicorn

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
RUNTIME = Path(os.environ['PAN_E2E_RUNTIME']).resolve()
if ROOT not in RUNTIME.parents:
    raise RuntimeError('Owned fixture must stay inside this worktree')
original_run = uvicorn.Server.run


def run(server, *args, **kwargs):
    app = server.config.app
    seeded = False
    @app.middleware('http')
    async def seed(request, call_next):
        nonlocal seeded
        service = getattr(app.state, 'job_final_service', None)
        if not seeded and service:
            seeded = True
            from packages.actions import store
            from packages.actions.group_migration import LegacyGroupMigration, enroll_fresh_source, command_definition
            from packages.actions.groups import Groups
            from packages.jobs.final_service import object_ref
            from packages.jobs import final_contracts as fc
            root = RUNTIME / 'cooperative-actions'
            enroll_fresh_source(root)
            store.root = lambda: root
            scripts = []
            for index in range(2):
                script = RUNTIME / ('command-' + str(index) + '.py')
                script.write_text('from pathlib import Path\np=Path(' + repr(str(RUNTIME / 'effects')) + ')\np.write_text("effect")\nprint("owned")\n', 'utf-8')
                scripts.append(script)
            old = store.Store(root)
            old.put('actions', {'id': 'old_alias', 'name': 'Owned old series', 'enabled': True, 'effect': 'write_local',
                'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}, 'output_format': 'text',
                'execution': {'kind': 'command', 'executable': sys.executable, 'args': [str(scripts[0])], 'cwd': str(RUNTIME)}})
            old.put('groups', {'id': 'p6_tools', 'name': 'Owned Group', 'action_ids': ['old_alias']})
            built = old.build('p6_tools')
            raw = old.read()['actions']['old_alias']
            value = command_definition(raw)
            ref = object_ref('action', service.publish_action('p6_original', value))
            maps = [{'groupId': 'p6_tools', 'version': v, 'actionId': 'old_alias', 'definitionDigest': fc.digest(raw), 'actionRef': ref}
                    for v in ('draft', built['version'])]
            who = {'type': 'user', 'id': 'local'}
            migration = LegacyGroupMigration(service, root)
            migration.apply(migration.preview(maps, actor=who), 'owned-adopt', actor=who)
            value['execution']['config'] = {'cwd': str(RUNTIME), 'legacyOutputMerge': 'concat/v1', 'commands': [
                {'argv': [sys.executable, str(scripts[0])]}, {'argv': [sys.executable, str(scripts[1])], 'timeout': 19}]}
            changed = service.write('action', ref['id'], {'definition': value, 'expectedRevision': ref['revision'], 'requestKey': 'owned-series'}, actor=who)['result']['object']
            service.control('action', ref['id'], {'operation': 'publish', 'expectedRevision': changed['revision'],
                'expectedStateRevision': changed['stateRevision'], 'requestKey': 'owned-publish', 'parameters': {}}, actor=who)
            (RUNTIME / 'p6-seed.json').write_text(json.dumps({'sourceSha': fc.digest(old.read()), 'groupRelease': built['version'], 'ref': ref}), 'utf-8')
        return await call_next(request)
    return original_run(server, *args, **kwargs)


uvicorn.Server.run = run
spec = importlib.util.spec_from_file_location('p6_existing_fixture', ROOT / 'packages/web/e2e/server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.main()
