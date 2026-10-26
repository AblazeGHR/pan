"""Real stdio -> private IPC -> canonical ASGI object routes, with no user credential in MCP.

R5-P7a retires the legacy four-object write tool.  ``authorization_objects``
carried one integer ``expected_revision`` and one object id; it could not express
``expectedStateRevision``, a Run identity or the canonical operation envelope, so
any default it invented would bypass CAS or conflate a retry with a new Run.  It
now projects only the retained read surface and refuses every write.

Successor assertions run through the canonical tools that already carry the full
envelope: ``job_*``/``trigger_*``/``event_creator_*``/``job_template_*``,
``job_runs``/``job_run``/``job_run_get``/``job_run_cancel`` and
``job_operation_find``.  Those tools reach the canonical ``/api`` routes through
the same trusted IPC bridge, so the "no user Bearer in MCP" property is asserted
on the successor path instead of disappearing with the retired one.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from packages.automation.authorization import bind_objects, object_path, object_request
from packages.core.manifest_loader import ManifestConfig, McpServer
from packages.core.session import Session
from packages.jobs import final_api
from packages.plugins.ipc import Bridge, request as ipc_request
from packages.plugins.runtime import Runtime as PluginRuntime
from tests.support.canonical_host import build
from tests.test_automation_core import async_test

SESSION_ID = 'ses_authorization_test'


def grants(**overrides):
    """Every canonical Agent grant on, with per-row overrides."""
    value = {kind: {name: True for name in names}
             for kind, names in final_api.CanonicalUserAuth.PERMISSION_SCHEMA.items()}
    value['action']['manageSystemActions'] = True
    for kind, row in overrides.items():
        value[kind].update(row)
    return value


def job_definition(action_ref, **fields):
    """A one-step canonical Job definition bound to the owned Action revision."""
    return {'name': 'owned-job',
            'steps': [{'stepId': 's0', 'actionRef': action_ref,
                       'arguments': {'sessionId': {'from': 'literal', 'value': 'owned-session'},
                                     'text': {'from': 'literal', 'value': 'once'}},
                       'completion': {'contract': 'durable-queue-accepted'}}],
            'retention': {'policyRef': 'test'},
            'overlap': {'mode': 'queue', 'maxActive': 1, 'maxPending': 16},
            **fields}


def trigger_definition(job_id, **fields):
    return {'name': 'owned-trigger', 'eventTypes': ['workers.all_idle'],
            'creatorSelector': {'mode': 'registeredType', 'providerType': 'core'},
            'targetJobId': job_id, **fields}


def template_definition(action_ref, **fields):
    return {'name': 'owned-template', 'jobDefinition': job_definition(action_ref),
            'composition': {}, **fields}


def event_creator_definition(action_ref, **fields):
    return {'name': 'core-workers',
            'providerRef': {'type': 'core', 'id': 'workers',
                            'releaseDigest': 'a' * 64, 'generation': 1},
            'configSchemaRef': action_ref, 'config': {},
            'eventTypes': ['workers.all_idle'], 'requiredCapabilities': [], **fields}


def harness(root):
    """Canonical host plus the trusted stdio/IPC bridge an MCP client uses."""
    host = build(root)
    manifest = ManifestConfig(mcp_servers=[
        McpServer('pan', command=sys.executable, args=['-m', 'packages.mcp.server'])])
    session = Session(id=SESSION_ID, name='Authorization', adapter_config={
        'plugin_bindings': [{'plugin_id': 'pan', 'mode': 'standalone', 'enabled': True}]})
    runtime = PluginRuntime(lambda: manifest, lambda sid: session if sid == SESSION_ID else None)
    bind_objects(runtime, host.app)
    bridge = Bridge(runtime, root)
    return SimpleNamespace(host=host, app=host.app, svc=host.automation,
                           service=host.service, action=host.action, bridge=bridge,
                           session=session, root=root)


async def close(h):
    await h.bridge.close()
    await h.host.automation.close()
    await h.host.service.close()


def result(value):
    if value.structuredContent is not None:
        return value.structuredContent
    return json.loads(value.content[0].text)


@pytest.mark.parametrize('projection', ['core', 'native'])
@async_test
async def test_real_stdio_canonical_objects_without_user_bearer(tmp_path, projection):
    h = harness(tmp_path)
    args = ['-m', 'packages.mcp.server'] if projection == 'core' else [
        '-m', 'packages.plugins.mcp', '--view', 'native', '--core']
    params = StdioServerParameters(command=sys.executable, args=args, cwd=str(Path.cwd()), env={
        **os.environ, 'PAN_PLUGIN_IPC': h.bridge.address, 'PAN_AGENT_SESSION_ID': SESSION_ID,
        # No HTTP operation for these tools, never touch an application port.
        'PAN_API_URL': 'http://127.0.0.1:1'})
    before_sessions = set(h.svc.auth.sessions)
    try:
        async with stdio_client(params) as streams, ClientSession(*streams) as client:
            await client.initialize()
            async def call(tool, **arguments):
                return result(await client.call_tool(tool, arguments))

            # Default profile: every Agent grant is off, so the canonical route denies.
            denied = await call('job_list')
            assert denied['error']['code'] == 'authorization_denied', denied

            h.svc.auth.change_permissions(grants(), 0)
            assert (await call('job_list'))['items'] == []
            assert 'owned_message' in {item['id'] for item in (await call('job_action_list'))['items']}

            # Compatibility reads use canonical pages. Missing Run identity or
            # stateRevision in the old tool can never manufacture a write.
            for kind in ('job', 'trigger', 'jobTemplate', 'action'):
                page = await call('authorization_objects', kind=kind, operation='read')
                assert 'items' in page, page
                retired = await call('authorization_objects', kind=kind, operation='create')
                assert retired['error']['code'] == 'retired', retired
            assert (await call('job_list'))['items'] == []

            created = await call('job_create', definition=job_definition(h.action),
                                 request_key='create')
            assert created['status'] == 'completed', created
            job = created['result']['object']

            updated = await call('job_update', object_id=job['id'],
                                 definition=job_definition(h.action, name='renamed'),
                                 expected_revision=job['configRevision'], request_key='update')
            assert updated['result']['object']['name'] == 'renamed', updated

            enabled = await call('job_control', object_id=job['id'], operation='enable',
                                 expected_revision=updated['result']['object']['configRevision'],
                                 expected_state_revision=updated['result']['object']['stateRevision'],
                                 request_key='enable')
            assert enabled['result']['object']['state'] == 'enabled', enabled
            job = enabled['result']['object']

            launched = await call('job_run', job_id=job['id'],
                                  expected_revision=job['configRevision'], request_key='run-1')
            assert launched['status'] == 'accepted', launched
            run_id = launched['runId']
            assert (await call('job_runs', job_id=job['id']))['items'][0]['runId'] == run_id
            run = await call('job_run_get', run_id=run_id)
            assert run['runId'] == run_id

            # The original operation is recoverable by identity, never replayed.
            found = await call('job_operation_find', operation='create', request_key='create')
            assert found['operationId'] == created['operationId'], found
            assert (await call('job_operation_get',
                               operation_id=created['operationId']))['status'] == 'completed'

            cancelled = await call('job_run_cancel', run_id=run_id,
                                   expected_state_revision=run['stateRevision'],
                                   request_key='cancel-1')
            assert cancelled['runId'] == run_id, cancelled

            trigger = (await call('trigger_create', definition=trigger_definition(job['id']),
                                  request_key='trigger'))['result']['object']
            assert trigger['targetJobId'] == job['id']
            switched = await call('trigger_control', object_id=trigger['id'], operation='enable',
                                  expected_revision=trigger['revision'],
                                  expected_state_revision=trigger['stateRevision'],
                                  request_key='trigger-enable')
            assert switched['result']['object']['state'] == 'enabled', switched
            assert (await call('trigger_get', object_id=trigger['id']))['state'] == 'enabled'
            retired = await call('trigger_retire', object_id=trigger['id'],
                                 expected_revision=switched['result']['object']['revision'],
                                 expected_state_revision=switched['result']['object']['stateRevision'],
                                 request_key='trigger-retire')
            assert retired['status'] in {'completed', 'unknown'}, retired

            template = (await call('job_template_create',
                                   definition=template_definition(h.action),
                                   request_key='template'))['result']['object']
            assert (await call('job_template_get', object_id=template['id']))['name'] == 'owned-template'
            assert (await call('job_template_retire', object_id=template['id'],
                               expected_revision=template['revision'],
                               expected_state_revision=template['stateRevision'],
                               request_key='template-retire'))['status'] in {'completed', 'unknown'}

            # Event Creator grants reach the canonical route; only the provider
            # binding is outside this fixture's scope.
            assert (await call('event_creator_list'))['items'] == []
            creator = await call('event_creator_create',
                                 definition=event_creator_definition(h.action),
                                 request_key='event-creator')
            assert creator['error']['code'] == 'provider_unavailable', creator

            retired_job = await call('job_retire', object_id=job['id'],
                                     expected_revision=job['configRevision'],
                                     expected_state_revision=job['stateRevision'],
                                     request_key='retire')
            assert retired_job['status'] in {'completed', 'unknown'}, retired_job

            # MCP never acquires a user's password or Bearer session.
            assert set(h.svc.auth.sessions) == before_sessions
    finally:
        await close(h)


def test_legacy_four_object_write_tool_keeps_only_retained_reads():
    # The retained read projection still answers for discovery.
    assert object_request('trigger', 'read') == ('GET', '/api/triggers', None)
    assert object_request('job', 'read') == ('GET', '/api/jobs', None)
    assert object_request('action', 'read') == ('GET', '/api/jobs/actions', None)
    assert object_request('jobTemplate', 'read') == ('GET', '/api/job-templates', None)
    # Every retired write is refused before transport; none is silently
    # defaulted into a canonical envelope.
    for kind, operation in (('trigger', 'create'), ('trigger', 'updateConfig'),
                            ('trigger', 'enable'), ('trigger', 'disable'),
                            ('trigger', 'delete'),
                            ('action', 'create'), ('action', 'updateConfig'),
                            ('action', 'delete'),
                            ('jobTemplate', 'create'), ('jobTemplate', 'updateConfig'),
                            ('jobTemplate', 'use'), ('jobTemplate', 'delete'),
                            ('job', 'create'), ('job', 'updateConfig'),
                            ('job', 'enable'), ('job', 'disable'), ('job', 'pause'),
                            ('job', 'resume'), ('job', 'cancel'), ('job', 'execute'),
                            ('job', 'retry'), ('job', 'confirmRecovery'), ('job', 'delete')):
        with pytest.raises(ValueError):
            object_request(kind, operation, {}, 'object_id', 1, 'stable')
    with pytest.raises(ValueError):
        object_request('unknown', 'read')


def test_object_path_allowlist_is_canonical_plus_retained_reads():
    # Canonical resources own every write method.
    for path in ('/api/jobs', '/api/jobs/j1', '/api/jobs/j1/runs', '/api/triggers',
                 '/api/triggers/t1', '/api/triggers/t1/operations', '/api/event-creators',
                 '/api/event-creators/c1/operations', '/api/job-templates/tl1',
                 '/api/job-runs/r1', '/api/job-runs/r1/operations', '/api/operations',
                 '/api/operations/o1', '/api/jobs/actions'):
        for method in ('GET', 'POST', 'PUT', 'DELETE', 'PATCH'):
            assert object_path(path, method), (path, method)
    # Retained historical reads stay readable and stay non-writable.
    for path in ('/api/jobs/action-jobs', '/api/jobs/action-jobs/schema',
                 '/api/jobs/action-jobs/j1/runs', '/api/jobs/action-references/a1',
                 '/api/jobs/job-templates', '/api/actions',
                 '/api/actions/session-operation-template', '/api/background-jobs',
                 '/api/background-jobs/b1', '/api/scheduler/tasks',
                 '/api/scheduler/tasks/t1', '/api/scheduler/tasks/t1/runs',
                 '/api/scheduler/next', '/api/scheduler/status'):
        for method in ('GET', 'HEAD', 'OPTIONS'):
            assert object_path(path, method), (path, method)
        for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            assert not object_path(path, method), (path, method)
    # Explicit historical cancels remain the only retained legacy POST.
    for path in ('/api/jobs/action-jobs/j1/cancel', '/api/background-jobs/b1/cancel'):
        assert object_path(path, 'POST')
        assert not object_path(path, 'GET')
    # Retired legacy writes are not addressable at all.
    for path in ('/api/actions/actions', '/api/actions/groups', '/api/automation/triggers',
                 '/api/action-drafts/actions/put', '/api/action-drafts/actions/delete',
                 '/api/final/jobs', '/api/final/actions',
                 '/api/automation/runs/r1/cancel', '/api/session-message-jobs',
                 '/api/jobs/action-jobs/j1/run-now', '/api/jobs/action-jobs/j1/retry',
                 '/api/jobs/action-jobs/j1/lifecycle', '/api/jobs/job-templates/tl1/instantiate',
                 '/api/scheduler/tasks/t1/pause', '/api/scheduler/tasks/t1/run-now',
                 '/api/background-jobs/b1/retry', '/api/automation/authorization/permissions',
                 '/api/main/exit', '/api/final/jobs/private/x'):
        assert not object_path(path), path
        assert not object_path(path, 'POST'), path
    # The retained surface stays describable without granting a write.
    assert object_path('/api/jobs/action-jobs')
    assert not object_path('/api/jobs/action-jobs', 'POST')
    assert object_path('/api/jobs')
    for path in ('https://bad/api/jobs', '//bad/api/jobs', '/api/jobs#fragment', '/api/main/exit'):
        assert not object_path(path)


@async_test
async def test_retired_writes_refuse_raw_ipc_and_never_fall_back_to_http(tmp_path, monkeypatch):
    from packages.mcp import server
    h = harness(tmp_path)
    monkeypatch.setattr(server, '_terminal_transport', 'stdio')
    monkeypatch.setattr(server.mcp, 'get_context', lambda: SimpleNamespace(
        request_context=SimpleNamespace(request=None)))
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: pytest.fail('No HTTP fallback'))
    monkeypatch.setattr('packages.plugins.ipc.request', lambda *a, **kw: pytest.fail('No retired IPC write'))
    try:
        before = h.svc.ledger.read()
        for path in ('/api/jobs/action-jobs', '/api/jobs/job-templates',
                     '/api/jobs/action-jobs/old/retry', '/api/actions/actions',
                     '/api/action-drafts/actions/put', '/api/automation/triggers',
                     '/api/background-jobs', '/api/session-message-jobs',
                     '/api/scheduler/tasks', '/api/final/jobs'):
            result = await asyncio.to_thread(ipc_request, {
                'session_id': SESSION_ID, 'operation': 'authorization_objects',
                'method': 'POST', 'path': path, 'body': {}}, h.bridge.address)
            assert result['error']['code'] == 403, (path, result)
            assert server._api('POST', path, {})['error']['code'] == 403, path
        assert h.svc.ledger.read() == before
        assert not h.service.list('job', actor={'type': 'user', 'id': 'local'})['items']
        assert not h.host.provider.calls
    finally:
        await close(h)


CANONICAL_CAPABILITIES = (
    ('read', 'GET', '/api/jobs'),
    ('create', 'POST', '/api/jobs'),
    ('updateConfig', 'PUT', '/api/jobs/job_x'),
    ('delete', 'DELETE', '/api/jobs/job_x'),
)
TRIGGER_CAPABILITIES = (
    ('read', 'GET', '/api/triggers'),
    ('create', 'POST', '/api/triggers'),
)
OTHER_CAPABILITIES = (
    ('jobTemplate', 'read', 'GET', '/api/job-templates'),
    ('eventCreator', 'read', 'GET', '/api/event-creators'),
)


@pytest.mark.parametrize('capability,method,path', CANONICAL_CAPABILITIES)
@async_test
async def test_every_job_capability_maps_to_denial_over_ipc(tmp_path, capability, method, path):
    h = harness(tmp_path)
    try:
        h.svc.auth.change_permissions(grants(job={capability: False}), 0)
        response = await asyncio.to_thread(
            ipc_request,
            {'session_id': SESSION_ID, 'operation': 'authorization_objects', 'method': method,
             'path': path, 'body': {'definition': job_definition(h.action), 'expectedRevision': 1,
                                    'requestKey': 'denied'}}, h.bridge.address)
        assert response['error']['code'] == 'authorization_denied', response
    finally:
        await close(h)


@pytest.mark.parametrize('capability,method,path', TRIGGER_CAPABILITIES)
@async_test
async def test_every_trigger_capability_maps_to_denial_over_ipc(tmp_path, capability, method, path):
    h = harness(tmp_path)
    try:
        h.svc.auth.change_permissions(grants(trigger={capability: False}), 0)
        response = await asyncio.to_thread(
            ipc_request,
            {'session_id': SESSION_ID, 'operation': 'authorization_objects', 'method': method,
             'path': path, 'body': {'definition': trigger_definition('job_x'), 'expectedRevision': 0,
                                    'requestKey': 'denied'}}, h.bridge.address)
        assert response['error']['code'] == 'authorization_denied', response
    finally:
        await close(h)


@pytest.mark.parametrize('kind,capability,method,path', OTHER_CAPABILITIES)
@async_test
async def test_template_and_event_creator_reads_map_to_denial_over_ipc(tmp_path, kind, capability,
                                                                     method, path):
    h = harness(tmp_path)
    try:
        h.svc.auth.change_permissions(grants(**{kind: {capability: False}}), 0)
        response = await asyncio.to_thread(
            ipc_request,
            {'session_id': SESSION_ID, 'operation': 'authorization_objects', 'method': method,
             'path': path, 'body': None}, h.bridge.address)
        assert response['error']['code'] == 'authorization_denied', response
    finally:
        await close(h)
