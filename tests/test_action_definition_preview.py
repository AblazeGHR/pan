"""Definition preview for the reused quick-create Action form.

The designed quick-create form produces a legacy-shaped Action draft. This
read-only adapter reuses the already approved adoption mapping
(`group_migration.command_definition`) so the form can create canonical
definitions without hand-written JSON and without a second mapping to drift.
It never persists, publishes or spawns anything.
"""
import sys

from packages.jobs.final_service import object_ref
from tests.test_action_unification_p1 import action_http
from tests.test_action_unification_p3 import USER, install

SCHEMA = {'type': 'object', 'properties': {'text': {'type': 'string'}},
          'required': ['text'], 'additionalProperties': False}


def draft(**patch):
    value = {
        'name': 'Quick echo',
        'description': 'Echo one argument',
        'enabled': False,
        'effect': 'read',
        'output_format': 'text',
        'input_schema': dict(SCHEMA),
        'execution': {'kind': 'api', 'method': 'GET', 'path': '/api/sessions'},
    }
    value.update(patch)
    return value


def preview(client, value, **extra):
    return client.post('/api/jobs/actions/definition-preview', json={'draft': value, **extra})


def test_api_draft_previews_a_canonical_definition_without_persisting(action_http):
    client, _ = action_http
    before = client.get('/api/jobs/actions').json()['items']
    response = preview(client, draft())
    assert response.status_code == 200, response.text
    definition = response.json()['definition']
    assert definition['execution'] == {'operation': 'api.get',
                                       'config': {'method': 'GET', 'path': '/api/sessions'}}
    assert definition['name'] == 'Quick echo'
    assert definition['effectClass'] == 'read'
    assert definition['inputSchema'] == SCHEMA
    assert definition['system'] is False
    after = client.get('/api/jobs/actions').json()['items']
    assert [item['id'] for item in after] == [item['id'] for item in before]


def test_command_and_session_drafts_map_to_their_approved_successors(action_http, tmp_path):
    client, _ = action_http
    command = preview(client, draft(
        name='Quick command',
        execution={'kind': 'command', 'executable': sys.executable,
                   'args': ['-c', 'print(1)'], 'cwd': str(tmp_path), 'timeout_sec': 9}))
    assert command.status_code == 200, command.text
    value = command.json()['definition']
    assert value['execution']['operation'] == 'process.command'
    assert value['execution']['config'] == {'executable': sys.executable, 'args': ['-c', 'print(1)'],
                                            'cwd': str(tmp_path), 'timeout_sec': 9}

    session = preview(client, draft(
        name='Quick pause', effect='write_local', output_format='json',
        input_schema={'type': 'object', 'properties': {}, 'additionalProperties': False},
        execution={'kind': 'session_operation', 'operation': 'pause_everything',
                   'selector': {'mode': 'explicit', 'ids': []}}))
    assert session.status_code == 200, session.text
    assert session.json()['definition']['execution']['operation'] == 'session.pause-everything'
    assert session.json()['definition']['effectClass'] == 'write_local'


def test_template_draft_supports_explicit_refs_and_names_the_canonical_gap(action_http):
    """Adopted legacy templates work; canonically created ones are a named gap."""
    client, service = action_http
    ref = install(service)
    template = service.write('template', None, {
        'definition': {
            'name': 'Quick template',
            'description': 'one step',
            'jobDefinition': {'name': 'Quick template job', 'steps': [{
                'stepId': 'effect', 'actionRef': ref, 'arguments': {},
                'completion': {'contract': 'process-exit'}}],
                'retention': {'policyRef': 'default'}},
            'composition': {'creators': [], 'triggers': []},
        },
        'expectedRevision': 0, 'requestKey': 'quick-template'}, actor=USER)['result']['object']
    published = service.control('template', template['id'], {
        'operation': 'publish', 'expectedRevision': template['revision'],
        'expectedStateRevision': template['stateRevision'], 'parameters': {},
        'requestKey': 'quick-template-publish'}, actor=USER)['result']['object']
    template_ref = object_ref('template', published)
    fixed = {'kind': 'job_template', 'templateId': 'jtpl_migrated', 'templateRevision': 1}
    legacy_draft = {'name': 'Quick template Action', 'description': 'one step',
                    'enabled': False, 'effect': 'write_local', 'output_format': 'json',
                    'input_schema': {'type': 'object', 'properties': {},
                                     'additionalProperties': False},
                    'execution': fixed}

    explicit = preview(client, legacy_draft, templateRef=template_ref)
    assert explicit.status_code == 200, explicit.text
    value = explicit.json()['definition']
    assert value['execution']['operation'] == 'template.instantiate'
    assert value['execution']['config']['templateRef'] == template_ref
    assert value['execution']['config']['legacyTemplate'] == fixed

    # Resolution by id only works for a canonical Template stored under that id.
    assert preview(client, legacy_draft).status_code == 404

    # Named gap: a canonically created Template (`template_*`) cannot be the
    # target of the legacy job_template quick type; the approved successor is
    # the legacy adoption mapping, which requires a legacy `jtpl_` id.
    canonical = preview(client, {**legacy_draft, 'execution': {
        'kind': 'job_template', 'templateId': published['id'],
        'templateRevision': published['revision']}})
    assert canonical.status_code == 400
    assert canonical.json()['error']['code'] == 'invalid_schema'


def test_preview_rejects_protected_invalid_and_unknown_drafts(action_http, tmp_path):
    client, _ = action_http
    protected = preview(client, draft(effect='high_risk', execution={
        'kind': 'command', 'executable': sys.executable, 'args': [], 'cwd': str(tmp_path)}))
    assert protected.status_code == 409
    assert protected.json()['error']['code'] == 'reference_conflict'

    unnamed = preview(client, draft(name='   '))
    assert unnamed.status_code == 400
    assert unnamed.json()['error']['code'] == 'invalid_schema'

    assert preview(client, {**draft(), 'unexpected': 1}).status_code == 400

    missing_template = preview(client, draft(execution={
        'kind': 'job_template', 'templateId': 'never_created', 'templateRevision': 1}))
    assert missing_template.status_code == 404


def test_definition_preview_requires_the_action_read_grant(action_http):
    client, _ = action_http
    response = client.post('/api/jobs/actions/definition-preview', json={'draft': draft()},
                           headers={'Authorization': 'Bearer not-a-user'})
    assert response.status_code == 401
