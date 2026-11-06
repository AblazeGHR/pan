"""Owned P5 fixture using the real Pan host and production assets."""
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
    raise RuntimeError('Fixture runtime must stay inside this worktree')
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
            from packages.actions.groups import Groups
            from packages.actions.command_execution import product_definition
            from packages.jobs.final_service import object_ref
            who = {'type': 'user', 'id': 'local'}
            script = RUNTIME / 'effect.py'
            script.write_text('import pathlib,sys\np=pathlib.Path(sys.argv[1]);p.write_text(p.read_text()+"effect\\n" if p.exists() else "effect\\n")\nprint("OUTPUT-"+sys.argv[2])\nprint("STDERR-owned",file=sys.stderr)\nprint("PARAM-"+sys.argv[4])\nsys.exit(int(sys.argv[3]))\n', encoding='utf-8')

            def action(name, output, exit_code=0):
                value = product_definition('process.command')
                value.update(system=False, name=name, description='P5 owned fixture')
                value['inputSchema'] = {'type': 'object', 'properties': {'suffix': {'type': 'string', 'default': 'default-suffix'}}, 'additionalProperties': False}
                value['execution']['config'] = {'argv': [sys.executable, str(script), str(RUNTIME / (name+'.effects')), output, str(exit_code), '{suffix}'], 'cwd': str(RUNTIME)}
                return value

            first = object_ref('action', service.publish_action('p5_echo', action('P5 Echo', 'v1')))
            groups = Groups(service)
            groups.put({'id': 'p5_tools', 'name': 'P5 Tools', 'tools': [{'alias': 'echo', 'actionRef': first}]}, 0, 'seed-group', actor=who)
            groups.publish('p5_tools', 1, 'seed-build', actor=who)
            item = service.get('action', 'p5_echo', actor=who)
            edited = service.write('action', 'p5_echo', {'definition': action('P5 Echo', 'v2'), 'expectedRevision': item['revision'], 'requestKey': 'seed-edit'}, actor=who)['result']['object']
            service.control('action', 'p5_echo', {'operation': 'publish', 'expectedRevision': edited['revision'], 'expectedStateRevision': edited['stateRevision'], 'requestKey': 'seed-publish', 'parameters': {}}, actor=who)
            service.publish_action('p5_failure', action('P5 Failure', 'failure', 7))
            protected = action('P5 Protected', 'forbidden')
            protected['effectClass'] = 'high_risk'
            service.publish_action('p5_protected', protected)
            service.publish_action('p5_unknown', action('P5 Unknown', 'unknown'))
            execute = service.executor.execute_commands
            async def lose_terminal_receipt(**values):
                result = await execute(**values)
                if any('unknown' in str(command) for command in values['prepared_commands']):
                    raise RuntimeError('P5 fault: effect/checkpoint completed before terminal receipt')
                return result
            service.executor.execute_commands = lose_terminal_receipt
            from packages.actions.store import Store
            legacy = Store()
            state = legacy.read()
            legacy.put('actions', {'id': 'p5_old_read', 'name': 'P5 old read', 'description': 'Owned legacy fixture', 'enabled': False, 'effect': 'read', 'output_format': 'json', 'input_schema': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}, 'execution': {'kind': 'api', 'method': 'GET', 'path': '/api/sessions'}}, expected=state['revision'], create=True, authorized=True)
            legacy.put('groups', {'id': 'p5_legacy', 'name': 'P5 Legacy', 'action_ids': ['p5_old_read']}, expected=legacy.read()['revision'], create=True, authorized=True)
            with service.transaction(readonly=True) as db:
                descriptor = service._action_version_record(db, 'p5_echo', 1, digest=first['digest'])['definition']
            job = {'name': 'P5 Job', 'steps': [{'stepId': 'echo', 'actionRef': first, 'arguments': {}, 'completion': {'contract': descriptor['defaultCompletion']}}], 'retention': {'policyRef': 'default'}}
            created = service.write('job', None, {'definition': job, 'expectedRevision': 0, 'requestKey': 'seed-job'}, actor=who)['result']['object']
            service.control('job', created['id'], {'operation': 'enable', 'expectedRevision': created['configRevision'], 'expectedStateRevision': created['stateRevision'], 'requestKey': 'seed-job-enable', 'parameters': {}}, actor=who)
            (RUNTIME / 'seed.json').write_text(json.dumps({'jobId': created['id'], 'first': first}), encoding='utf-8')
            seeded = True
        return await call_next(request)
    return original_run(server, *args, **kwargs)


uvicorn.Server.run = run
spec = importlib.util.spec_from_file_location('p5_existing_fixture', ROOT / 'packages/web/e2e/server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.main()


