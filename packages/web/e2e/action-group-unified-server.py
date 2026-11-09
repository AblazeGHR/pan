"""Owned fixture for the unified Action/Group directory in a production UI.

The browser talks to the real FastAPI routes and the real build assets. This
launcher only seeds isolated records inside its own runtime directory:

- one canonical Action with two published versions,
- one canonical Group that fixed version 1 and produced an immutable release,
- one adopted legacy Action/Group pair (the original compatibility projection).

A legacy-only pair cannot be seeded after cutover: the owner epoch fence refuses
every post-cutover legacy write, which is the product's intended behavior.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import uvicorn

ROOT = Path(os.environ.get('PAN_E2E_SOURCE_ROOT') or Path(__file__).resolve().parents[3]).resolve()
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
            from packages.actions.group_migration import (
                LegacyGroupMigration,
                command_definition,
                enroll_fresh_source,
            )
            from packages.jobs.final_service import object_ref
            from packages.jobs import final_contracts as fc

            who = {'type': 'user', 'id': 'local'}
            root = RUNTIME / 'cooperative-actions'
            enroll_fresh_source(root)
            store.root = lambda: root
            script = RUNTIME / 'unified-command.py'
            script.write_text(
                'from pathlib import Path\np=Path(' + repr(str(RUNTIME / 'effects')) + ')\n'
                'p.write_text("effect")\nprint("owned")\n',
                'utf-8',
            )
            workspace = str(RUNTIME)

            # 1. Canonical Action: created here so both versions are published.
            value = command_definition({
                'id': 'uni_echo',
                'name': 'Unified Echo',
                'description': 'Canonical command Action',
                'enabled': True,
                'effect': 'write_local',
                'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                'output_format': 'text',
                'execution': {'kind': 'command', 'executable': sys.executable,
                              'args': [str(script)], 'cwd': workspace},
            })
            created = service.write('action', None, {'definition': value, 'expectedRevision': 0,
                                                     'requestKey': 'unified-action'}, actor=who)['result']['object']
            first = service.control('action', created['id'], {
                'operation': 'publish', 'expectedRevision': created['revision'],
                'expectedStateRevision': created['stateRevision'],
                'requestKey': 'unified-action-publish-1', 'parameters': {}}, actor=who)['result']['object']
            ref_v1 = object_ref('action', first)

            value['description'] = 'Canonical command Action (second revision)'
            changed = service.write('action', created['id'], {
                'definition': value, 'expectedRevision': first['revision'],
                'requestKey': 'unified-action-v2'}, actor=who)['result']['object']
            second = service.control('action', created['id'], {
                'operation': 'publish', 'expectedRevision': changed['revision'],
                'expectedStateRevision': changed['stateRevision'],
                'requestKey': 'unified-action-publish-2', 'parameters': {}}, actor=who)['result']['object']
            ref_v2 = object_ref('action', second)

            # 2. Canonical Group fixing version 1, then an explicit build.
            from packages.actions.groups import Groups
            groups = Groups(service)
            groups.put({'id': 'uni_tools', 'name': 'Unified Tools',
                        'tools': [{'alias': 'echo', 'actionRef': ref_v1}]}, 0, 'unified-group', actor=who)
            with service.transaction(readonly=True) as db:
                head = groups.get(db, 'uni_tools')
            first_build = groups.publish('uni_tools', head['revision'], 'unified-group-build', actor=who)
            # A second committed release proves the history read is a real
            # projection of immutable documents, not a pointer alias.
            second_build = groups.publish('uni_tools', first_build['result']['group']['revision'],
                                          'unified-group-build-2', actor=who)
            published = second_build
            release = second_build['result']['release']

            # 3. Adopted legacy pair (original compatibility projection).
            from tests.support.legacy_action_source import LegacySource
            old = LegacySource(root)
            old.put('actions', {'id': 'old_alias', 'name': 'Owned old series', 'enabled': True,
                'effect': 'write_local',
                'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                'output_format': 'text',
                'execution': {'kind': 'command', 'executable': sys.executable,
                              'args': [str(script)], 'cwd': workspace}})
            old.put('groups', {'id': 'adopted_tools', 'name': 'Adopted Group', 'action_ids': ['old_alias']})
            built = old.build('adopted_tools')
            raw = old.read()['actions']['old_alias']
            mapped_ref = object_ref('action', service.publish_action('adopted_original', command_definition(raw)))
            maps = [{'groupId': 'adopted_tools', 'version': version, 'actionId': 'old_alias',
                     'definitionDigest': fc.digest(raw), 'actionRef': mapped_ref}
                    for version in ('draft', built['version'])]
            migration = LegacyGroupMigration(service, root)
            migration.apply(migration.preview(maps, actor=who), 'owned-adopt', actor=who)

            (RUNTIME / 'unified-seed.json').write_text(json.dumps({
                'actionId': created['id'], 'ref': ref_v2, 'fixed': ref_v1,
                'actionRevision': second['revision'], 'actionStateRevision': second['stateRevision'],
                'groupId': 'uni_tools', 'groupRevision': published['result']['group']['revision'],
                'release': release['version'], 'firstRelease': first_build['result']['release']['version'],
                'firstDigest': first_build['result']['release']['digest'],
                'mappedRef': mapped_ref,
                'legacyRelease': built['version'], 'sourceSha': fc.digest(old.read()),
            }), 'utf-8')
        return await call_next(request)

    return original_run(server, *args, **kwargs)


uvicorn.Server.run = run
spec = importlib.util.spec_from_file_location('unified_existing_fixture', ROOT / 'packages/web/e2e/server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.main()
