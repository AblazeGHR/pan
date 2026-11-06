"""Actual legacy Template registry/graph adoption, with no production profile."""
import asyncio
import copy

import pytest

from packages.actions.group_migration import LegacyGroupMigration, LegacyGroupCompatibility, binding_key
from packages.actions.legacy_templates import mapped_definition
from packages.jobs import final_contracts as fc
from packages.jobs.action_templates import Templates
from packages.jobs.final_service import FinalService, object_ref
from tests.test_action_unification_p1 import action_http
from tests.test_action_unification_p3 import USER
from tests.test_action_unification_p6 import source, own


def registry(action_http, tmp_path, monkeypatch, *, schedules=None):
    from packages.actions.group_migration import command_definition
    _, service = action_http
    old, built, mappings, echo = source(tmp_path, service, monkeypatch)
    templates = Templates(old)
    cfg = {'name': 'Original configured Job', 'actionId': 'echo', 'arguments': {'value': 'from-template'},
           'schedule': [], 'trigger': {'kind': 'manual'}, 'enabled': False, 'maxRuns': 7,
           'overlap': {'mode': 'queue', 'parallelLimit': 4},
           'recovery': {'enabled': False, 'monitoring': False, 'maxAttempts': 5,
                        'backoffSec': [2, 7, 19], 'stableResetSec': 91},
           'notification': {'on': ['completed', 'unknown', 'cleaning'], 'target': 'job'}}
    if schedules is not None:
        cfg.update(schedule=schedules, trigger={'kind': 'schedule'})
    created = templates.put({'id': 'jtpl_original', 'name': 'Original Template', 'description': 'Keep metadata', 'config': cfg}, 0)
    # A real deleted Template leaves only a monotonic counter, not a phantom
    # historical definition that the successor could claim to have imported.
    deleted = templates.put({'id': 'jtpl_deleted', 'name': 'Deleted', 'config': cfg}, 0)
    templates.delete(deleted['id'], deleted['revision'])
    state = old.read()
    raw = state['jobTemplates'][created['id']]
    policies = {'overlap': {'mode': 'queue', 'maxActive': 1, 'maxPending': 42},
        'recovery': {'automatic': False, 'maxAttempts': 5, 'backoffSeconds': ['2', '7', '19'], 'stableResetSeconds': '91'},
        'notification': {'on': ['completed', 'uncertain', 'settling'], 'recipients': []},
        'retention': {'policyRef': 'reviewed_retention'}}
    from packages.scheduler.providers import TimeProvider
    provider = TimeProvider()
    scheduler = {'advanceMode': 'grid', 'dstPolicy': 'gap-skip-fold-first', 'graceSeconds': '60',
        'pausePolicy': 'advance', 'providerRef': {'type': 'time', 'id': 'scheduler',
        'releaseDigest': provider.release_digest, 'generation': provider.generation},
        'configSchemaRef': provider.config_schema_ref, 'requiredCapabilities': list(provider.capabilities)}
    contexts = [{'policy': scheduler} for _ in cfg['schedule']]
    definition = mapped_definition(raw, state, {'echo': {'object': service.get('action', echo['id'], actor=USER),
        'ref': echo, 'completions': {'ordinary': {'contract': 'process-exit'}}}}, policies, contexts)
    target = service.write('template', None, {'definition': definition, 'expectedRevision': 0, 'requestKey': 'mapped-template'}, actor=USER)['result']['object']
    target = service.control('template', target['id'], {'operation': 'publish', 'expectedRevision': target['revision'],
        'expectedStateRevision': target['stateRevision'], 'parameters': {}, 'requestKey': 'publish-template'}, actor=USER)['result']['object']
    template_ref = object_ref('template', target)
    action = {'id': 'instantiate', 'name': 'Original instantiate', 'enabled': True, 'effect': 'write_local',
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'execution': {'kind': 'job_template', 'templateId': raw['id'], 'templateRevision': raw['revision']}}
    old.put('actions', action, authorized=True)
    action = old.read()['actions']['instantiate']
    ref = object_ref('action', service.publish_action('old_instantiate', command_definition(action, template_ref=template_ref)))
    mappings += [{'groupId': None, 'version': 'draft', 'actionId': 'instantiate', 'definitionDigest': fc.digest(action), 'actionRef': ref},
                 {'kind': 'template', 'templateId': raw['id'], 'revision': raw['revision'], 'definitionDigest': fc.digest(raw),
                  'templateRef': template_ref, 'policies': policies, 'schedules': contexts}]
    own(service)
    return service, old, built, mappings, templates, raw, ref, target


def test_P6_TEMPLATE_02_actual_registry_graph_policy_tombstone_handoff_cold_no_replay(action_http, tmp_path, monkeypatch):
    service, old, built, maps, templates, raw, ref, target = registry(action_http, tmp_path, monkeypatch)
    migration = LegacyGroupMigration(service, old.root)
    before = old.path.read_bytes()
    plan = migration.preview(maps, actor=USER)
    assert plan['blocked'] == []
    assert plan['templateVersions'] == {'jtpl_original': 1, 'jtpl_deleted': 2}
    assert plan['templates'][0]['raw'] == raw
    adopted = migration.apply(plan, 'template-graph-adopt', actor=USER)
    with pytest.raises(fc.ContractError, match='fence'):
        templates.delete('jtpl_original', 1)
    with service.transaction(readonly=True) as db:
        row = service._get(db, 'legacy_template_binding', binding_key(old.root, raw['id']))
        assert row['adoptionOperationId'] == adopted['operationId'] and row['raw'] == raw
    # Canonical published Templates are immutable. Keep that original contract;
    # legacy edits must create a reviewed successor under the same old alias.
    changed = copy.deepcopy(plan['templates'][0]['template'])
    definition = {key: changed[key] for key in fc.schema_artifacts()['template_definition.schema.json']['properties'] if key in changed}
    definition['jobDefinition']['name'] = 'New current name'
    with pytest.raises(fc.ContractError, match='Immutable'):
        service.write('template', target['id'], {'definition': definition, 'expectedRevision': target['revision'], 'requestKey': 'later-template-edit'}, actor=USER)
    body = {'actionRef': ref, 'arguments': {}, 'requestKey': 'instantiate-once'}
    result = asyncio.run(service.invoke_action(ref['id'], body, actor=USER))
    assert result['result']['output']['templateId'] == raw['id']
    job_id = result['result']['output']['jobId']
    job = service.get('job', job_id, actor=USER)
    assert job['name'] == raw['config']['name'] and job['runLimit']['maxRuns'] == 7
    assert job['overlap'] == {'mode': 'queue', 'maxActive': 1, 'maxPending': 42}
    assert job['recovery']['backoffSeconds'] == ['2', '7', '19']
    assert job['notification']['on'] == ['completed', 'uncertain', 'settling']
    cold = FinalService(service.root, service.auth)
    replay = asyncio.run(cold.invoke_action(ref['id'], body, actor=USER))
    assert replay['replayed'] and replay['operationId'] == result['operationId']
    compat = LegacyGroupCompatibility(service, old.root)
    action = {**old.read()['actions']['instantiate'], 'name': 'Edited old Action'}
    assert compat.put_action(action, None, 'old-template-action-edit', actor=USER)['result']['actionRef']['id'] == ref['id']
    with service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 1
    assert old.path.read_bytes() == before and not (tmp_path / 'effects').exists()


@pytest.mark.parametrize('mutation', ['policy', 'edge', 'counter', 'unknown', 'revision_bool', 'generation_bool'])
def test_P6_TEMPLATE_03_mismatch_graph_policy_unknown_preserves_whole_source(action_http, tmp_path, monkeypatch, mutation):
    service, old, _, maps, _, _, _, _ = registry(action_http, tmp_path, monkeypatch)
    if mutation == 'policy':
        maps[-1]['policies']['recovery']['stableResetSeconds'] = '92'
    else:
        def change(state):
            if mutation == 'edge':
                state['jobTemplates']['jtpl_original']['config']['actionId'] = 'missing'
            elif mutation == 'counter':
                state['templateVersions']['jtpl_original'] = 0
            elif mutation == 'revision_bool':
                state['jobTemplates']['jtpl_original']['revision'] = True
            elif mutation == 'generation_bool':
                state['jobTemplates']['jtpl_original']['referenceGeneration'] = False
            else:
                state['jobTemplates']['jtpl_original']['unknownPolicy'] = True
        old.mutate(change)
        if mutation in {'revision_bool', 'generation_bool'}:
            malformed = old.read()['jobTemplates']['jtpl_original']
            maps[-1].update(revision=malformed['revision'], definitionDigest=fc.digest(malformed))
    before = old.path.read_bytes()
    migration = LegacyGroupMigration(service, old.root)
    plan = migration.preview(maps, actor=USER)
    assert any(b.get('family') == 'jobTemplates' for b in plan['blocked'])
    with pytest.raises(fc.ContractError):
        migration.apply(plan, 'must-preserve', actor=USER)
    assert old.path.read_bytes() == before
    with service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='legacy_template_binding'").fetchone()[0] == 0


def test_P6_TEMPLATE_04_multiple_original_schedules_disabled_record_kept_native_composition(action_http, tmp_path, monkeypatch):
    schedules = [{'kind': 'interval', 'intervalSec': 3600, 'anchor': '2030-01-01T00:00:00+00:00', 'misfirePolicy': 'skip'},
                 {'kind': 'cron', 'cron': '0 7 * * *', 'timezone': 'Asia/Singapore'},
                 {'kind': 'cron', 'cron': '0 9 * * *', 'timezone': 'Asia/Singapore', 'enabled': False}]
    service, old, _, maps, _, raw, ref, _ = registry(action_http, tmp_path, monkeypatch, schedules=schedules)
    migration = LegacyGroupMigration(service, old.root)
    before = old.path.read_bytes()
    plan = migration.preview(maps, actor=USER)
    assert plan['blocked'] == []
    assert plan['templates'][0]['raw']['config']['schedule'] == schedules
    migration.apply(plan, 'schedule-template-adopt', actor=USER)
    result = asyncio.run(service.invoke_action(ref['id'], {'actionRef': ref, 'arguments': {}, 'requestKey': 'schedule-create'}, actor=USER))
    original = service.operation(result['result']['compositionOperationId'], actor=USER)
    assert len(original['result']['objectIds']['creators']) == 2
    assert len(original['result']['objectIds']['triggers']) == 2
    assert original['result']['activated'] is False
    assert old.path.read_bytes() == before


@pytest.mark.parametrize('phase', ['source-frozen', 'before-commit', 'after-commit'])
def test_P6_TEMPLATE_05_cutover_reuses_original_operation_and_never_instantiates(action_http, tmp_path, monkeypatch, phase):
    service, old, _, maps, _, _, _, _ = registry(action_http, tmp_path, monkeypatch)
    migration = LegacyGroupMigration(service, old.root)
    plan = migration.preview(maps, actor=USER)
    before = old.path.read_bytes()
    def fault(at):
        if at == phase:
            raise RuntimeError('template-cutover')
    with pytest.raises(RuntimeError, match='template-cutover'):
        migration.apply(plan, 'original-template-cutover', actor=USER, fault=fault)
    if phase != 'after-commit':
        with service.transaction(readonly=True) as db:
            assert db.execute("SELECT count(*) FROM final_documents WHERE kind='legacy_template_binding'").fetchone()[0] == 0
    recovered = migration.apply(plan, 'original-template-cutover', actor=USER)
    assert migration.status('original-template-cutover', actor=USER)['operationId'] == recovered['operationId']
    assert old.path.read_bytes() == before
    with service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0


def test_P6_TEMPLATE_06_nested_graph_adoption_and_old_action_cycle_guard(action_http, tmp_path, monkeypatch):
    service, old, _, maps, templates, raw, ref, _ = registry(action_http, tmp_path, monkeypatch)
    config = {**copy.deepcopy(raw['config']), 'actionId': 'instantiate', 'arguments': {}}
    nested = templates.put({'id': 'jtpl_nested', 'name': 'Nested original Template', 'config': config}, 0)
    state = old.read()
    source_record = state['jobTemplates'][nested['id']]
    bindings = {}
    for aid, action_ref in [('echo', maps[0]['actionRef']), ('instantiate', ref)]:
        obj = service.get('action', action_ref['id'], actor=USER)
        bindings[aid] = {'object': obj, 'ref': action_ref, 'completions': {'ordinary': {'contract': obj['defaultCompletion']}}}
    definition = mapped_definition(source_record, state, bindings, maps[-1]['policies'], [])
    target = service.write('template', None, {'definition': definition, 'expectedRevision': 0, 'requestKey': 'nested-create'}, actor=USER)['result']['object']
    target = service.control('template', target['id'], {'operation': 'publish', 'expectedRevision': target['revision'],
        'expectedStateRevision': target['stateRevision'], 'requestKey': 'nested-publish', 'parameters': {}}, actor=USER)['result']['object']
    maps.append({'kind': 'template', 'templateId': source_record['id'], 'revision': source_record['revision'],
        'definitionDigest': fc.digest(source_record), 'templateRef': object_ref('template', target),
        'policies': maps[-1]['policies'], 'schedules': []})
    migration = LegacyGroupMigration(service, old.root)
    plan = migration.preview(maps, actor=USER)
    assert plan['blocked'] == [] and len(plan['templates']) == 2
    migration.apply(plan, 'nested-adopt', actor=USER)
    compat = LegacyGroupCompatibility(service, old.root)
    loop = {**old.read()['actions']['instantiate'], 'execution': {'kind': 'job_template',
            'templateId': source_record['id'], 'templateRevision': source_record['revision']}}
    with pytest.raises(ValueError, match='cycle'):
        compat.put_action(loop, None, 'must-not-create-cycle', actor=USER)
    assert service.action_ref(ref['id'], actor=USER) == ref


def test_P6_TEMPLATE_07_shared_ref_cannot_choose_other_source_activation_policy(action_http, tmp_path, monkeypatch):
    from packages.actions.legacy_templates import review
    service, old, _, maps, _, raw, _, _ = registry(action_http, tmp_path, monkeypatch)
    migration = LegacyGroupMigration(service, old.root)
    migration.apply(migration.preview(maps, actor=USER), 'first-template-binding', actor=USER)
    state = copy.deepcopy(old.read())
    state['jobTemplates'][raw['id']]['config']['enabled'] = True
    reviewed = copy.deepcopy(maps)
    reviewed[-1]['definitionDigest'] = fc.digest(state['jobTemplates'][raw['id']])
    with service.transaction(readonly=True) as db:
        with pytest.raises(fc.ContractError, match='different original instantiation policy'):
            review(service, db, state, reviewed)
