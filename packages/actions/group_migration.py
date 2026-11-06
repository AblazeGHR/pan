"""Offline, source-bound Group adoption using the existing owner and receipts.

Only enrolled cooperative registries are eligible. Enrollment is possible only
before the first registry exists; it cannot attest an arbitrary existing writer.
Source freeze is durable before the single canonical authority commit. A crash
in between is blocked/query-only until the same reviewed plan is resumed.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from packages.jobs import final_contracts as fc
from packages.jobs.runtime_owner import OwnerFence
from packages.jobs.final_runtime.ledger import principal
from .store import Store, validate_action, identifier, _lock
from .command_execution import product_definition

SOURCE_KEY = 'action-group-cooperative-source-v1'
RESERVED = {'argv', 'commands', 'cwd', 'env', 'command', 'executable', 'args',
            'timeout', 'timeout_sec', 'hideWindow', 'externalScript', 'interpreter', 'argumentBinding'}


def fail(message, code='reference_conflict'):
    raise fc.ContractError(code, message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def enroll_fresh_source(root):
    """Explicit bootstrap for a fresh, cooperative source; never existing data."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    owner = OwnerFence(root)
    owner.lock.acquire()
    try:
        with owner.transaction() as db:
            db.execute('INSERT INTO metadata VALUES(?,?)', (SOURCE_KEY, fc.canonical_json({
                'sourceRoot': str(root), 'protocol': SOURCE_KEY})))
    finally:
        owner.lock.release()


def source_enrolled(root):
    root = Path(root).resolve()
    if not (root / 'job-runtime-owner.sqlite3').exists():
        return False
    path = root / 'job-runtime-owner.sqlite3'
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT value FROM metadata WHERE key=?', (SOURCE_KEY,)).fetchone()
        return bool(row and json.loads(row[0]) == {'sourceRoot': str(root), 'protocol': SOURCE_KEY})


def source_authority(root):
    path = Path(root) / 'job-runtime-owner.sqlite3'
    if not path.exists():
        return 0, None
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT epoch FROM owner WHERE singleton=1').fetchone()
        intent = db.execute("SELECT value FROM metadata WHERE key='activation'").fetchone()
        return row[0] if row else 0, intent[0] if intent else None


@contextmanager
def legacy_writer(root):
    """One OS writer plus durable lease, including generic JSON mutations.

    Pre-enrollment registries retain their old behavior, and are ineligible for
    adoption. Abandoned leases are intentionally never cleared by this adapter.
    """
    owner = OwnerFence(Path(root).resolve())
    if not owner.path.exists():
        yield
        return
    owner.lock.acquire()
    try:
        with owner.legacy_pass():
            yield
    finally:
        owner.lock.release()


def command_definition(raw, *, template_ref=None):
    action = validate_action(raw, automation=True)
    if action['execution']['kind'] == 'job_template':
        from .legacy_families import template_successor
        return template_successor(action, template_ref)
    if action['execution']['kind'] == 'system_job':
        from .legacy_families import system_successor
        return system_successor(action)
    if (action['system'] or action['monitoring'] or action['recoverable']
            or action['effect'] == 'high_risk'):
        fail('Protected legacy Actions require their existing host policy')
    if action['execution']['kind'] in {'api', 'session_operation'}:
        kind = action['execution']['kind']
        provider = 'pan.api' if kind == 'api' else 'pan.session-operation'
        value = product_definition('api.get' if kind == 'api' else 'session.pause-everything')
        value.update(providerRef={'id': provider, 'releaseDigest': fc.digest(provider + '.v1'), 'generation': 1},
                     requiredCapabilities=[], cancel={'supported': False})
        value.update(name=action['name'], description=action['description'], system=False,
                     inputSchema=action['input_schema'], effectClass=action['effect'],
                     retry={**value['retry'], 'idempotent': action['idempotent']})
        value['execution']['config'] = {k: v for k, v in action['execution'].items() if k != 'kind'}
        from .legacy_families import completion_contract
        return fc.validate_definition('action', completion_contract(value, 'provider-receipt'))
    if action['execution']['kind'] != 'command':
        fail('Legacy Action requires its named host adapter')
    fixed_binding = bool(RESERVED & set(action['input_schema'].get('properties', {}))
                         or set(action['input_schema']) - {'type', 'properties', 'required', 'additionalProperties', 'description', 'title'})
    value = product_definition('process.command')
    value.update(name=action['name'], description=action['description'], system=False, inputSchema=action['input_schema'],
                 effectClass=action['effect'], retry={**value['retry'], 'idempotent': action['idempotent']})
    value['execution']['config'] = copy.deepcopy(action['execution'])
    value['execution']['config'].pop('kind')
    if fixed_binding:
        value['execution']['config']['argumentBinding'] = 'legacy-flat/v1'
    return fc.validate_definition('action', value)


def legacy_action_view(value, base, enabled):
    """Lossless single-command descriptor projection, never a new executor."""
    config = copy.deepcopy(value.get('execution', {}).get('config', {}))
    if value.get('execution', {}).get('operation') == 'template.instantiate':
        from .legacy_families import template_view
        return template_view(value, base, enabled)
    if value.get('execution', {}).get('operation', '').startswith('legacy.'):
        from .legacy_families import system_view
        return system_view(value, enabled)
    if value.get('execution', {}).get('operation') in {'api.get', 'session.pause-everything'}:
        kind = 'api' if value['execution']['operation'] == 'api.get' else 'session_operation'
        raw = {**copy.deepcopy(base), 'name': value['name'], 'description': value['description'],
               'enabled': enabled, 'effect': value['effectClass'], 'input_schema': value['inputSchema'],
               'idempotent': value['retry']['idempotent'], 'execution': {'kind': kind, **config}}
        if command_definition(raw) != value:
            fail('Canonical contracts differ from the legacy family schema')
        return validate_action(raw, automation=True)
    if value.get('execution', {}).get('operation') != 'process.command':
        fail('Canonical operation has no legacy single-command representation')
    commands = config.pop('commands', None)
    if commands is not None:
        if not isinstance(commands, list) or len(commands) != 1 or not isinstance(commands[0], dict):
            fail('Multiple commands have no legacy single-output merge contract')
        config.update(commands[0])
    env = config.pop('env', {})
    if not isinstance(env, dict) or env or config.pop('hideWindow', True) is not True:
        fail('Nonempty environment or visible-window policy has no legacy command field')
    if 'timeout' in config:
        timeout = config.pop('timeout')
        if type(timeout) not in (int, float) or not 1 <= timeout <= 30 or timeout != int(timeout):
            fail('Timeout cannot be represented by legacy integer timeout_sec 1..30')
        config['timeout_sec'] = int(timeout)
    if 'argv' in config:
        argv = config.pop('argv')
        if not isinstance(argv, list) or not argv:
            fail('Invalid fixed argv')
        config['executable'], config['args'] = argv[0], argv[1:]
    if set(config) - {'executable', 'args', 'cwd', 'timeout_sec', 'argumentBinding'}:
        fail('Canonical command descriptor contains fields absent from the legacy schema')
    raw = {**copy.deepcopy(base), 'name': value['name'], 'description': value['description'],
           'enabled': enabled, 'effect': value['effectClass'], 'input_schema': value['inputSchema'],
           'idempotent': value['retry']['idempotent'],
           'execution': {'kind': 'command', **{k: v for k, v in config.items() if k != 'argumentBinding'}}}
    expected = command_definition(raw)
    if config.get('argumentBinding') == 'legacy-flat/v1' and 'argumentBinding' not in expected['execution']['config']:
        config.pop('argumentBinding')
    normalized = {**value, 'execution': {**value['execution'], 'config': config}}
    if expected != normalized:
        fail('Canonical Action contracts or business argument binding differ from the legacy command schema')
    return raw


def flat_release_contract(value, base):
    """Validate an explicit serial-output successor without flattening CRUD.

    Old command output was one ordered byte stream. The reviewed concat/v1
    declaration applies that rule to canonical serial stdout, parsing JSON only
    after concatenation. Individual command receipts remain separately stored.
    """
    config = copy.deepcopy(value['execution']['config'])
    merge = config.pop('legacyOutputMerge', None)
    if merge is None:
        return validate_action(legacy_action_view(value, base, True), automation=False)
    if merge != 'concat/v1' or value['execution']['operation'] != 'process.command':
        fail('Unknown legacy output merge declaration')
    commands = config.pop('commands', None)
    if not isinstance(commands, list) or not 1 <= len(commands) <= 32:
        fail('Explicit serial command list required for concat/v1')
    for command in commands:
        if not isinstance(command, dict):
            fail('Invalid serial command descriptor')
        single = {**value, 'execution': {**value['execution'], 'config': {**config, **command}}}
        validate_action(legacy_action_view(single, base, True), automation=False)
    return {'outputMerge': merge}


class LegacyGroupMigration:
    def __init__(self, service, source_root):
        self.service = service
        self.store = Store(Path(source_root).resolve())

    def status(self, request_key, *, actor):
        try:
            return self.service.operation_for_key('update', request_key, actor=actor)
        except KeyError:
            self.service._permit(actor, 'action', 'read')
            path = self.store.root / 'job-runtime-owner.sqlite3'
            if not path.exists():
                raise KeyError(request_key)
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                row = db.execute("SELECT value FROM metadata WHERE key='group-handoff-operation'").fetchone()
            if not row:
                raise KeyError(request_key)
            intent = json.loads(row[0])
            if intent['requestKey'] != request_key or intent['principal'] != principal(actor):
                raise KeyError(request_key)
            return {**intent, 'status': 'blocked', 'phase': 'source-frozen', 'operationId': None}

    def preview(self, mappings, *, actor):
        """Read-only bounded plan. Mapping keys explicitly identify each snapshot.

        Each mapping is {groupId, version ('draft' or old version), actionId,
        definitionDigest, actionRef}. Equality of IDs or a P1 alias is insufficient.
        """
        self.service._permit(actor, 'action', 'read')
        self.service._permit(actor, 'action', 'updateConfig')
        if not isinstance(mappings, list) or len(mappings) > 1000:
            fail('Bounded explicit mappings required', 'invalid_schema')
        if not self.store.path.is_file() or self.store.path.is_symlink():
            fail('Explicit plain registry.json source required')
        raw = self.store.path.read_bytes()
        if len(raw) > 4 * 1024 * 1024:
            fail('Registry exceeds offline bound', 'capacity_exceeded')
        state = fc.loads(raw.decode('utf-8'))
        blockers, groups, files = [], [], {'registry.json': hashlib.sha256(raw).hexdigest()}
        if not source_enrolled(self.store.root):
            blockers.append({'reason': 'unknown_writer'})
        if set(state) - {'revision', 'actions', 'groups', 'retired_plugins', 'actionRevisions', 'actionLifecycle', 'actionGenerations',
                         'jobTemplates', 'templateVersions'}:
            blockers.append({'reason': 'unknown_registry_fields'})
        if not isinstance(state.get('groups'), dict) or not 1 <= len(state['groups']) <= 100:
            fail('Registry Group scope exceeds bound or is malformed')
        with self.service.transaction(readonly=True) as db:
            for gid, raw_group in sorted(state['groups'].items()):
                try:
                    group = copy.deepcopy(raw_group)
                    identifier(gid)
                    if (set(group) - {'id', 'name', 'action_ids', 'build', 'last_build'}
                            or group.get('id') != gid or not isinstance(group.get('action_ids'), list)
                            or not isinstance(group.get('name'), str) or not group['name'].strip()
                            or not 1 <= len(group['action_ids']) <= 100
                            or len(set(group['action_ids'])) != len(group['action_ids'])):
                        fail('Complex or damaged Group')
                    drafts = [copy.deepcopy(state['actions'][aid]) for aid in group['action_ids']]
                    for draft in drafts:
                        draft['enabled'] = state.get('actionLifecycle', {}).get(draft['id'], draft.get('enabled', False))
                    versions = [('draft', drafts, None, group['name'])]
                    directory = self.store.root / 'builds' / gid
                    paths = sorted(directory.iterdir()) if directory.exists() else []
                    if len(paths) > 100:
                        fail('Historical releases exceed bound')
                    for path in paths:
                        if path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(self.store.root):
                            fail('Unknown historical artifact')
                        if {p.name for p in path.iterdir()} != {'snapshot.json', 'capabilities.json'}:
                            fail('Unknown historical release files')
                        snapshot, capability = path / 'snapshot.json', path / 'capabilities.json'
                        for file in (snapshot, capability):
                            if file.is_symlink() or file.stat().st_size > 4 * 1024 * 1024:
                                fail('Unsafe/oversized release artifact')
                            files[str(file.relative_to(self.store.root))] = sha(file)
                        document, caps = json.loads(snapshot.read_text('utf-8')), json.loads(capability.read_text('utf-8'))
                        if (set(document) != {'group', 'version', 'actions'} or document['version'] != path.name
                                or document['group'].get('id') != gid):
                            fail('Historical release identity mismatch')
                        expected_caps = legacy_document(gid, path.name, document['actions'])
                        if caps != expected_caps:
                            fail('Historical release capability mismatch')
                        versions.append((path.name, document['actions'], str(path.resolve()), document['group']['name']))
                    mapped = {}
                    for version, actions, path, name in versions:
                        tools = []
                        for action in actions:
                            validate_action(action, automation=False)
                            definition = command_definition(action)
                            entries = [m for m in mappings if m.get('groupId') == gid and m.get('version') == version
                                       and m.get('actionId') == action['id'] and m.get('definitionDigest') == fc.digest(action)]
                            if len(entries) != 1:
                                fail('Explicit snapshot mapping missing or ambiguous')
                            ref = entries[0]['actionRef']
                            from .groups import Groups
                            resolved = Groups(self.service).resolve(db, ref, available=False)
                            record = self.service._action_version_record(db, ref['id'], ref['revision'], digest=ref['digest'], for_ref=True)
                            if (resolved != definition or record['enabled'] != action.get('enabled', False)
                                    or record['state'] != 'published'):
                                fail('Snapshot definition or lifecycle mapping mismatch')
                            if version == 'draft' and self.service._get(db, 'action', ref['id'])['revision'] != ref['revision']:
                                fail('Current draft mapping must identify the current canonical head')
                            tools.append({'alias': action['id'], 'actionRef': ref, 'inputSchema': action['input_schema'],
                                          'description': action.get('description', action['name']),
                                          'outputSchema': definition['outputSchema'], 'outputFormat': action.get('output_format', 'json')})
                        if len({t['actionRef']['id'] for t in tools}) != len(tools):
                            fail('Multiple old tools map to the same logical Action')
                        mapped[version] = {'tools': tools, 'directory': path, 'name': name}
                    for key in ('build', 'last_build'):
                        build = group.get(key)
                        if build and (set(build) != {'plugin_id', 'version', 'directory', 'action_ids'}
                                      or build['plugin_id'] != 'action-group-' + gid.replace('_', '-')
                                      or build['version'] not in mapped or mapped[build['version']]['directory'] != build['directory']
                                      or build['action_ids'] != [t['alias'] for t in mapped[build['version']]['tools']]):
                            fail('Build pointer mismatch')
                    groups.append({'groupId': gid, 'source': group, 'versions': mapped})
                except (ValueError, KeyError, TypeError, OSError, fc.ContractError) as exc:
                    blockers.append({'groupId': gid, 'reason': str(exc)})
        referenced = {aid for g in state['groups'].values() if isinstance(g, dict) and isinstance(g.get('action_ids'), list)
                      for aid in g.get('action_ids', []) if isinstance(aid, str)}
        standalone = []
        with self.service.transaction(readonly=True) as db:
            for aid in sorted(set(state.get('actions', {})) - referenced):
                try:
                    action = copy.deepcopy(state['actions'][aid])
                    action['enabled'] = state.get('actionLifecycle', {}).get(aid, action.get('enabled', False))
                    entries = [m for m in mappings if m.get('groupId') is None and m.get('version') == 'draft'
                               and m.get('actionId') == aid and m.get('definitionDigest') == fc.digest(action)]
                    if len(entries) != 1:
                        fail('Unreferenced Action requires an explicit standalone draft mapping')
                    ref = entries[0]['actionRef']
                    from .groups import Groups
                    record = self.service._action_version_record(db, ref['id'], ref['revision'], digest=ref['digest'], for_ref=True)
                    definition = command_definition(action, template_ref=record['definition']['execution']['config'].get('templateRef'))
                    if (Groups(self.service).resolve(db, ref, available=False) != definition
                            or record['enabled'] != action['enabled'] or record['state'] != 'published'):
                        fail('Standalone Action definition/lifecycle mismatch')
                    if self.service._get(db, 'action', ref['id'])['revision'] != ref['revision']:
                        fail('Standalone draft mapping must identify the current canonical head')
                    standalone.append({'alias': aid, 'actionRef': ref})
                except (ValueError, KeyError, TypeError, OSError, fc.ContractError) as exc:
                    blockers.append({'actionId': aid, 'reason': str(exc)})
        templates = []
        if 'jobTemplates' in state or 'templateVersions' in state:
            try:
                self.service._permit(actor, 'template', 'read')
                self.service._permit(actor, 'template', 'updateConfig')
                from .legacy_templates import review
                with self.service.transaction(readonly=True) as db:
                    templates = review(self.service, db, state, mappings)
            except (ValueError, KeyError, TypeError, fc.ContractError) as exc:
                blockers.append({'family': 'jobTemplates', 'reason': str(exc)})
        plan = {'protocol': 'legacy-group-adoption/v1', 'sourceRoot': str(self.store.root),
                'canonicalRoot': str(self.service.root.resolve()), 'profileId': self.service.profile_id,
                'expectedSourceEpoch': source_authority(self.store.root)[0],
                'sourceRevision': state['revision'], 'files': files, 'groups': groups,
                'mappings': mappings, 'blocked': blockers}
        if standalone:
            plan['standaloneActions'] = standalone
        if 'jobTemplates' in state or 'templateVersions' in state:
            plan['templates'] = templates
            plan['templateVersions'] = state.get('templateVersions', {})
        return {**plan, 'planDigest': fc.digest(plan)}

    def apply(self, plan, request_key, *, actor, fault=lambda phase: None):
        self.service._permit(actor, 'action', 'updateConfig', execution=True)
        self.service._permit(actor, 'action', 'create', execution=True)
        self.service._permit(actor, 'action', 'lifecycle', execution=True)
        payload = {'legacyGroupAdoption': plan}
        # Query/replay the canonical receipt before touching a stopped source.
        with self.service.transaction(readonly=True) as db:
            row = db.execute('SELECT document FROM final_operations WHERE scope=? AND requestKey=?',
                             (self.service._scope(actor, 'update'), request_key)).fetchone()
            if row:
                old = json.loads(row[0])
                fc.idempotency_outcome({'scope': self.service._scope(actor, 'update'), 'requestDigest': old['requestDigest']},
                                      scope=self.service._scope(actor, 'update'), request_digest=fc.request_digest('update', payload))
                return {**old['response'], 'replayed': True}
        if not self.service.owner.token or self.service.owner.lock.file is None:
            fail('Canonical owner must be active')
        if not source_enrolled(self.store.root):
            fail('Unknown legacy writer; preserve source')
        intent = {'requestKey': request_key, 'principal': principal(actor),
                  'profileId': self.service.profile_id, 'planDigest': plan['planDigest']}
        intent_digest = fc.digest(intent)
        def matches_review():
            fresh = self.preview(plan['mappings'], actor=actor)
            epoch, prior_intent = source_authority(self.store.root)
            if prior_intent is not None and prior_intent != intent_digest:
                fail('Frozen source belongs to a different original requestKey/principal/plan')
            if epoch != plan.get('expectedSourceEpoch'):
                if prior_intent != intent_digest:
                    fail('Source authority generation changed without the original handoff intent')
                # Only the original reviewed operation can resume a frozen source.
                fresh['expectedSourceEpoch'] = plan['expectedSourceEpoch']
                fresh.pop('planDigest')
                fresh['planDigest'] = fc.digest(fresh)
            return fresh == plan
        if plan.get('blocked') or not matches_review():
            fail('Plan changed or contains preserved/blocked records')
        source_owner = OwnerFence(self.store.root)
        with _lock:
            status = source_owner.status()
            if (status and status['epoch'] != plan['expectedSourceEpoch']
                    and source_owner.activation_intent() != intent_digest):
                fail('Source authority generation changed without the original handoff intent')
            source_owner.claim(status['epoch'] if status else 0)
            try:
                # claim advanced the source epoch; establish the same original
                # plan intent before the post-freeze byte/mapping recheck.
                with source_owner.transaction() as source_db:
                    source_owner.activation_intent(intent_digest)
                    source_db.execute("INSERT OR IGNORE INTO metadata VALUES('group-handoff-operation',?)",
                                      (fc.canonical_json(intent),))
                if not matches_review() or plan['blocked']:
                    fail('Plan changed or contains preserved/blocked records')
                fault('source-frozen')
                from .groups import Groups
                with self.service.transaction() as db, self.service.auth.lock:
                    for capability in ('create', 'updateConfig', 'lifecycle'):
                        self.service._permit(actor, 'action', capability, execution=True)
                    if plan.get('templates'):
                        self.service._permit(actor, 'template', 'updateConfig', execution=True)
                    if not matches_review():
                        fail('Mapped definitions changed before the canonical authority commit')
                    def mutate(operation_id):
                        for entry in plan['groups']:
                            gid, source = entry['groupId'], entry['source']
                            if db.execute("SELECT 1 FROM final_documents WHERE kind='action_group' AND id=?", (gid,)).fetchone():
                                fail('Canonical Group identity already exists')
                            draft = entry['versions']['draft']['tools']
                            group = {'id': gid, 'name': source['name'], 'revision': 1,
                                     'tools': [{k: t[k] for k in ('alias', 'actionRef')} for t in draft],
                                     'compatibility': {'protocol': 'legacy-flat/v1',
                                         'outputFormats': {t['alias']: t['outputFormat'] for t in draft}}}
                            for version, mapped in entry['versions'].items():
                                if version == 'draft':
                                    continue
                                if db.execute("SELECT 1 FROM final_documents WHERE kind='action_group_release' AND id=?", (version,)).fetchone():
                                    fail('Historical release identity collision')
                                release = {'groupId': gid, 'name': mapped['name'], 'revision': 1,
                                           'tools': mapped['tools'], 'version': version, 'directory': mapped['directory'],
                                           'compatibility': {'protocol': 'legacy-flat/v1'}}
                                release['digest'] = fc.digest(release)
                                Groups.save(db, 'action_group_release', version, release)
                            if source.get('build'):
                                group['release'] = source['build']['version']
                            Groups.save(db, 'action_group', gid, group)
                            Groups.save(db, 'action_group_handoff', gid, {'generation': 1, 'sourceRoot': str(self.store.root),
                                'sourceDigest': plan['files']['registry.json'], 'planDigest': plan['planDigest'],
                                'operationId': operation_id, 'ownerEpoch': self.service.owner.token[0],
                                'sourceEpoch': source_owner.token[0], 'source': source, 'versions': entry['versions']})
                        root = str(self.store.root)
                        for template in plan.get('templates', []):
                            Groups.save(db, 'legacy_template_binding', binding_key(root, template['legacyId']), {
                                **template, 'sourceRoot': root, 'adoptionOperationId': operation_id,
                                'sourceDigest': plan['files']['registry.json'], 'deleted': False})
                        Groups.save(db, 'action_group_registry', root, {'revision': plan['sourceRevision'],
                            **({'templateVersions': plan['templateVersions']} if 'templateVersions' in plan else {})})
                        seed_action_bindings(db, self.store.root, self.store.read(), plan.get('standaloneActions', []))
                        fault('before-commit')
                        return {'groups': [e['groupId'] for e in plan['groups']], 'planDigest': plan['planDigest'],
                                'generation': 1, 'preserved': True}, [], False
                    response = self.service._operation(db, actor, 'update', request_key, payload, 'action', mutate)
                fault('after-commit')
                return response
            finally:
                # Closed/failed owner rows still reject every legacy writer.
                # Recovery reclaims only the same plan; no source lease reset.
                source_owner.release()


def legacy_document(gid, version, actions):
    pid = 'action-group-' + gid.replace('_', '-')
    return {'protocol': 'pan-capability/v1', 'plugin_id': pid,
            'provider': {'transport': 'stdio_jsonrpc', 'command_ref': pid + '-provider'},
            'capabilities': [{'id': pid + '.' + a['id'], 'export_key': a['id'],
                'description': a['description'] + ' [effect: ' + a['effect'] + ']',
                'contract_version': version, 'input_schema': a['input_schema'],
                'execution': {'operation': a['id'], 'result_format': 'mcp'}} for a in actions]}


def advance_projection(db, gid):
    row = db.execute("SELECT document FROM final_documents WHERE kind IN ('action_group_handoff','legacy_group_binding') AND id=?", (gid,)).fetchone()
    if row:
        root = json.loads(row[0])['sourceRoot']
        row = db.execute("SELECT document FROM final_documents WHERE kind='action_group_registry' AND id=?", (root,)).fetchone()
        if row:
            db.execute("UPDATE final_documents SET document=? WHERE kind='action_group_registry' AND id=?",
                       (fc.canonical_json({**json.loads(row[0]), 'revision': json.loads(row[0])['revision'] + 1}), root))


def binding_key(root, aid):
    return fc.digest([str(Path(root).resolve()), aid])


def advance_action_projection(db, action_id):
    roots = {json.loads(row[0])['sourceRoot'] for row in db.execute(
        "SELECT document FROM final_documents WHERE kind='legacy_action_binding' "
        "AND json_extract(document,'$.actionId')=? AND json_extract(document,'$.deleted')=0", (action_id,))}
    for root in roots:
        row = db.execute("SELECT document FROM final_documents WHERE kind='action_group_registry' AND id=?", (root,)).fetchone()
        if row:
            value = json.loads(row[0])
            db.execute("UPDATE final_documents SET document=? WHERE kind='action_group_registry' AND id=?",
                       (fc.canonical_json({**value, 'revision': value['revision'] + 1}), root))


def seed_action_bindings(db, root, state, standalone=()):
    """Add compatibility metadata to the same authority transaction, no JSON write.

    Older adopted sources are backfilled only from their original hashed source
    and committed per-snapshot mappings. Existing bindings are never inferred by
    matching names/IDs, overwritten, or resurrected after deletion.
    """
    from .groups import Groups
    root = str(Path(root).resolve())
    registry = json.loads(db.execute("SELECT document FROM final_documents WHERE kind='action_group_registry' AND id=?", (root,)).fetchone()[0])
    if registry.get('actionsInitialized'):
        return
    refs = {t['alias']: t['actionRef'] for t in standalone}
    for row in db.execute("SELECT document FROM final_documents WHERE kind='action_group_handoff'"):
        handoff = json.loads(row[0])
        if handoff['sourceRoot'] != root:
            continue
        if sha(Path(root) / 'registry.json') != handoff['sourceDigest']:
            fail('Original adopted Action source changed')
        for tool in handoff['versions']['draft']['tools']:
            aid, ref = tool['alias'], tool['actionRef']
            if aid in refs and refs[aid] != ref:
                fail('Conflicting current Action mappings across Groups')
            refs[aid] = ref
    if set(refs) != set(state['actions']):
        fail('Every source Action requires an explicit committed mapping')
    for aid, ref in refs.items():
        head = json.loads(db.execute("SELECT document FROM final_documents WHERE kind='action' AND id=?", (ref['id'],)).fetchone()[0])
        if (head['revision'] != ref['revision'] or head['state'] != 'published'
                or head['enabled'] != state['actions'][aid]['enabled']):
            fail('Original adopted Action changed before compatibility indexing; preserve and reconcile its original receipts')
        Groups.save(db, 'legacy_action_binding', binding_key(root, aid), {
            'sourceRoot': root, 'legacyId': aid, 'actionId': ref['id'],
            'raw': state['actions'][aid], 'deleted': False})
    Groups.save(db, 'action_group_registry', root, {**registry, 'actionsInitialized': True})


class LegacyGroupCompatibility:
    """Old Group shape/CAS -> the existing canonical Group operations."""
    def __init__(self, service, source_root):
        self.service, self.root = service, str(Path(source_root).resolve())

    def reconciliation_preview(self, *, actor):
        """Review original adoption identities against durable canonical receipts.

        This only repairs compatibility indexing for an already committed
        handoff. It cannot enroll a source, infer aliases, publish, or replay.
        """
        from packages.jobs.final_service import definition
        self.service._permit(actor, 'action', 'read')
        self.service._permit(actor, 'action', 'updateConfig')
        raw = Store(self.root).path.read_bytes()
        state = fc.loads(raw.decode('utf-8'))
        with self.service.transaction(readonly=True) as db:
            registry = self.service._get(db, 'action_group_registry', self.root)
            if registry.get('actionsInitialized'):
                fail('Compatibility indexing is already initialized')
            mappings, adoptions = {}, set()
            for row in db.execute("SELECT document FROM final_documents WHERE kind='action_group_handoff'"):
                handoff = json.loads(row[0])
                if handoff['sourceRoot'] != self.root:
                    continue
                if handoff['sourceDigest'] != hashlib.sha256(raw).hexdigest():
                    fail('Original adopted Action source changed')
                receipt = db.execute('SELECT document FROM final_operations WHERE id=?', (handoff['operationId'],)).fetchone()
                if (not receipt or json.loads(receipt[0])['response']['result'].get('planDigest') != handoff['planDigest']
                        or handoff['generation'] != 1):
                    fail('Original adoption receipt/generation is unproven')
                adoptions.add(handoff['operationId'])
                for tool in handoff['versions']['draft']['tools']:
                    aid, ref = tool['alias'], tool['actionRef']
                    if aid in mappings and mappings[aid] != ref:
                        fail('Conflicting original Action identities')
                    mappings[aid] = ref
            if not adoptions or set(mappings) != set(state['actions']):
                fail('Every Action needs its original committed adoption mapping')
            items = []
            for aid, ref in sorted(mappings.items()):
                original = self.service._action_version_record(db, ref['id'], ref['revision'], digest=ref['digest'])
                if original['definition'] != command_definition(state['actions'][aid],
                        template_ref=original['definition']['execution']['config'].get('templateRef')):
                    fail('Original mapped definition differs from preserved source')
                head = self.service._get(db, 'action', ref['id'])
                value = definition('action', head)
                projected = legacy_action_view(value, state['actions'][aid], head['enabled'] and head['state'] == 'published')
                proofs = []
                if (head['revision'] != ref['revision'] or head['state'] != 'published'
                        or head['enabled'] != state['actions'][aid]['enabled']):
                    for op_id, document in db.execute("SELECT id,document FROM final_operations WHERE json_extract(document,'$.kind')='action'"):
                        response = json.loads(document)['response']
                        if response['status'] == 'completed' and response.get('result', {}).get('object') == head:
                            proofs.append(op_id)
                    if not proofs:
                        fail('Changed head has no matching original canonical operation receipt')
                items.append({'legacyId': aid, 'originalRef': ref,
                              'currentRef': {'id': head['id'], 'revision': head['revision'], 'digest': fc.digest(value)},
                              'state': head['state'], 'stateRevision': head['stateRevision'], 'enabled': head['enabled'],
                              'receiptIds': sorted(proofs), 'projectionDigest': fc.digest(projected)})
            plan = {'protocol': 'legacy-action-reconciliation/v1', 'sourceRoot': self.root,
                    'sourceDigest': hashlib.sha256(raw).hexdigest(), 'expectedRevision': registry['revision'],
                    'profileId': self.service.profile_id, 'principal': principal(actor),
                    'authorizationRevision': self.service.auth.permissions_status()['revision'],
                    'adoptionOperationIds': sorted(adoptions), 'items': items}
            return {**plan, 'planDigest': fc.digest(plan)}

    def reconcile(self, plan, key, *, actor):
        from .groups import Groups
        self.service._permit(actor, 'action', 'read')
        fc.check_request_key(key)
        payload = {'legacyActionReconciliation': plan}
        with self.service.transaction(readonly=True) as db:
            row = db.execute('SELECT document FROM final_operations WHERE scope=? AND requestKey=?',
                             (self.service._scope(actor, 'update'), key)).fetchone()
            if row:
                prior = json.loads(row[0])
                fc.idempotency_outcome({'scope': self.service._scope(actor, 'update'),
                    'requestDigest': prior['requestDigest']}, scope=self.service._scope(actor, 'update'),
                    request_digest=fc.request_digest('update', payload))
                return {**prior['response'], 'replayed': True}
        with self.service.transaction() as db, self.service.auth.lock:
            def change(_):
                self.service._permit(actor, 'action', 'updateConfig', execution=True)
                if self.service.owner.token is None:
                    fail('Reconciliation requires the canonical owner', 'revision_conflict')
                if self.reconciliation_preview(actor=actor) != plan:
                    fail('Reconciliation plan changed; dry-run again', 'revision_conflict')
                state = Store(self.root).read()
                for item in plan['items']:
                    aid = item['legacyId']
                    Groups.save(db, 'legacy_action_binding', binding_key(self.root, aid), {
                        'sourceRoot': self.root, 'legacyId': aid, 'actionId': item['originalRef']['id'],
                        'raw': state['actions'][aid], 'deleted': False})
                registry = self.check_revision(db, plan['expectedRevision'])
                Groups.save(db, 'action_group_registry', self.root, {**registry,
                    'revision': registry['revision'] + 1, 'actionsInitialized': True})
                return {'reconciliation': plan, 'preserved': True}, [], False
            return self.service._operation(db, actor, 'update', key, payload, 'action', change)

    def active(self):
        with self.service.transaction(readonly=True) as db:
            return bool(db.execute("SELECT 1 FROM final_documents WHERE kind='action_group_registry' AND id=?", (self.root,)).fetchone())

    def initialize(self, db):
        seed_action_bindings(db, self.root, Store(self.root).read())

    def binding(self, db, aid):
        value = self.service._get(db, 'legacy_action_binding', binding_key(self.root, aid))
        if value['deleted']:
            raise KeyError(aid)
        return value

    def action(self, db, binding):
        from packages.jobs.final_service import definition
        head = self.service._get(db, 'action', binding['actionId'])
        value = definition('action', head)
        raw = legacy_action_view(value, binding['raw'], head['enabled'] and head['state'] == 'published')
        return raw, {'id': head['id'], 'revision': head['revision'], 'digest': fc.digest(value)}

    def release_action(self, db, binding):
        """Old build can publish a reviewed serial successor without a fake
        single-command CRUD descriptor. Only fixed release metadata is needed.
        """
        from packages.jobs.final_service import definition
        head = self.service._get(db, 'action', binding['actionId'])
        value = definition('action', head)
        if value['execution']['config'].get('legacyOutputMerge') != 'concat/v1':
            return self.action(db, binding)
        flat_release_contract(value, binding['raw'])
        return {'id': binding['legacyId'], 'output_format': binding['raw']['output_format']}, {
            'id': head['id'], 'revision': head['revision'], 'digest': fc.digest(value)}

    def check_revision(self, db, expected):
        registry = self.service._get(db, 'action_group_registry', self.root)
        if expected is not None and (type(expected) is not int or expected != registry['revision']):
            fail('Legacy registry changed; reload before saving', 'revision_conflict')
        return registry

    def advance(self, db):
        from .groups import Groups
        registry = self.service._get(db, 'action_group_registry', self.root)
        Groups.save(db, 'action_group_registry', self.root, {**registry, 'revision': registry['revision'] + 1})

    def put_action(self, raw, expected, key, *, actor, draft=False, create=False, lifecycle_only=False):
        from .groups import Groups
        with self.service.transaction() as db, self.service.auth.lock:
            def change(_):
                self.check_revision(db, expected)
                self.initialize(db)
                value = copy.deepcopy(raw)
                if isinstance(value, dict) and 'id' not in value:
                    value['id'] = 'action_' + uuid.uuid4().hex[:24]
                item = validate_action(value)
                aid = item['id']
                try:
                    binding = self.binding(db, aid)
                except KeyError:
                    binding, old = None, None
                else:
                    try:
                        old, _ = self.action(db, binding)
                    except KeyError:
                        fail('Canonical Action binding is damaged; preserve original identity')
                if create and old is not None:
                    fail('ID already exists; update the existing item by ID')
                if draft:
                    item['enabled'] = False
                if item['system'] or (old and old['system']):
                    fail('System Actions are product-managed', 'authorization_denied')
                lifecycle = item['enabled'] if old is None else item['enabled'] != old['enabled']
                changed = old is None or {k: v for k, v in item.items() if k != 'enabled'} != {k: v for k, v in old.items() if k != 'enabled'}
                if lifecycle_only and changed:
                    fail('Lifecycle operation cannot edit Action configuration')
                capability = 'create' if old is None else 'updateConfig' if changed or not lifecycle else 'lifecycle'
                if lifecycle_only:
                    capability = 'lifecycle'
                self.service._permit(actor, 'action', capability, execution=True)
                if lifecycle:
                    self.service._permit(actor, 'action', 'lifecycle', execution=True)
                template_ref = None
                if item['execution']['kind'] == 'job_template':
                    from packages.jobs.action_templates import check_action_graph
                    graph = self.projection(Store(self.root).read())
                    graph['actions'][aid] = item
                    check_action_graph(graph, item)
                    # Only reuse an explicit original mapping. A jtpl_ name or
                    # equal revision is never evidence of canonical identity.
                    candidates = []
                    for row in db.execute("SELECT document FROM final_documents WHERE kind='legacy_template_binding' AND json_extract(document,'$.sourceRoot')=?", (self.root,)):
                        candidate = json.loads(row[0])
                        if (not candidate['deleted'] and candidate['raw']['id'] == item['execution']['templateId']
                                and candidate['raw']['revision'] == item['execution']['templateRevision']):
                            candidates.append(candidate['templateRef'])
                    for row in db.execute("SELECT document FROM final_documents WHERE kind='legacy_action_binding' AND json_extract(document,'$.sourceRoot')=?", (self.root,)):
                        candidate = json.loads(row[0])
                        if candidate['deleted']:
                            continue
                        mapped = self.service._get(db, 'action', candidate['actionId'])
                        cfg = mapped.get('execution', {}).get('config', {})
                        if cfg.get('legacyTemplate') == item['execution'] and 'templateRef' in cfg:
                            candidates.append(cfg['templateRef'])
                    if candidates and all(c == candidates[0] for c in candidates):
                        template_ref = candidates[0]
                definition = command_definition(item, template_ref=template_ref)
                inner = fc.digest(['legacy-action-draft', key])
                head = self.service._action_write(binding['actionId'] if binding else None,
                    {'definition': definition, 'expectedRevision': self.service._get(db, 'action', binding['actionId'])['revision'] if binding else 0,
                     'requestKey': inner}, actor=actor, draft_enabled=False, management_capability=capability, legacy_projection=False)['result']['object']
                if item['enabled']:
                    # Existing enabled config edits keep their original
                    # updateConfig grant; this is no new enable intent.
                    head = self.service._action_control(head['id'], {'operation': 'publish',
                        'expectedRevision': head['revision'], 'expectedStateRevision': head['stateRevision'],
                        'parameters': {}, 'requestKey': fc.digest(['legacy-action-publish', key])}, actor=actor,
                        management_capability='lifecycle' if lifecycle else capability, legacy_projection=False)['result']['object']
                Groups.save(db, 'legacy_action_binding', binding_key(self.root, aid), {
                    'sourceRoot': self.root, 'legacyId': aid, 'actionId': head['id'], 'raw': item, 'deleted': False})
                advance_action_projection(db, head['id'])
                return {'item': item, 'actionRef': {'id': head['id'], 'revision': head['revision'], 'digest': fc.digest(definition)}}, [], False
            return self.service._operation(db, actor, 'update', key,
                {'legacyActionPut': raw, 'expectedRevision': expected, 'draft': draft, 'create': create,
                 'lifecycleOnly': lifecycle_only}, 'action', change)

    def lifecycle_action(self, aid, enabled, expected, key, *, actor):
        if type(enabled) is not bool or type(expected) is not int:
            fail('Expected boolean enabled and integer revision', 'invalid_schema')
        with self.service.transaction() as db, self.service.auth.lock:
            self.service._permit(actor, 'action', 'lifecycle', execution=True)
            self.initialize(db)
            raw, ref = self.action(db, self.binding(db, aid))
            if raw['system']:
                def change(_):
                    self.check_revision(db, expected)
                    self.service._permit(actor, 'action', 'manageSystemActions', execution=True)
                    head = self.service._get(db, 'action', ref['id'])
                    self.service._action_control(ref['id'], {'operation': 'enable' if enabled else 'disable',
                        'expectedRevision': head['revision'], 'expectedStateRevision': head['stateRevision'],
                        'parameters': {}, 'requestKey': fc.digest(['legacy-system-lifecycle', key])}, actor=actor,
                        legacy_system_lifecycle=True)
                    return {'item': {**raw, 'enabled': enabled}, 'actionRef': ref}, [ref], False
                return self.service._operation(db, actor, 'update', key,
                    {'legacySystemLifecycle': aid, 'enabled': enabled, 'expectedRevision': expected}, 'action', change)
            # A new current version preserves every old release's immutable
            # enabled snapshot. Do not disable a historical pinned version.
            return self.put_action({**raw, 'enabled': enabled}, expected, key, actor=actor, lifecycle_only=True)

    def delete_action(self, aid, expected, key, *, actor, confirm_references=False):
        from .groups import Groups
        with self.service.transaction() as db, self.service.auth.lock:
            def change(operation_id):
                self.service._permit(actor, 'action', 'delete', execution=True)
                self.check_revision(db, expected)
                self.initialize(db)
                try:
                    binding = self.binding(db, aid)
                except KeyError:
                    fail('Item not found')
                logical_id = binding['actionId']
                other_alias = db.execute("SELECT 1 FROM final_documents WHERE kind='legacy_action_binding' "
                    "AND id<>? AND json_extract(document,'$.actionId')=? AND json_extract(document,'$.deleted')=0",
                    (binding_key(self.root, aid), logical_id)).fetchone()
                if db.execute("SELECT 1 FROM final_documents g,json_each(g.document,'$.tools') t WHERE g.kind='action_group' AND json_extract(t.value,'$.actionRef.id')=? "
                              "AND (?=0 OR json_extract(t.value,'$.alias')=?)", (logical_id, bool(other_alias), aid)).fetchone():
                    fail('Action is referenced by a Group; remove its references first')
                references = db.execute("SELECT 1 FROM final_documents d,json_tree(d.document) t WHERE d.kind IN ('job','template','trigger') AND t.key='actionRef' AND t.type='object' AND json_extract(t.value,'$.id')=? LIMIT 1", (logical_id,)).fetchone()
                if references and not other_alias and (actor['type'] != 'user' or not confirm_references):
                    fail('User Bearer and confirmReferences required to delete referenced Action', 'authorization_denied')
                if not other_alias:
                    head = self.service._get(db, 'action', logical_id)
                    from packages.jobs.final_service import definition
                    head = self.service._action_write(logical_id, {'definition': definition('action', head),
                        'expectedRevision': head['revision'], 'requestKey': fc.digest(['legacy-action-tombstone', key])},
                        actor=actor, draft_enabled=False, management_capability='delete', legacy_projection=False)['result']['object']
                    self.service._action_control(logical_id, {'operation': 'retire', 'expectedRevision': head['revision'],
                        'expectedStateRevision': head['stateRevision'], 'parameters': {},
                        'requestKey': fc.digest(['legacy-action-delete', key])}, actor=actor, management_capability='delete', legacy_projection=False)
                Groups.save(db, 'legacy_action_binding', binding_key(self.root, aid), {**binding, 'deleted': True})
                Groups.save(db, 'legacy_action_retirement', fc.digest([self.root, aid, logical_id]), {
                    'sourceRoot': self.root, 'legacyId': aid, 'actionId': logical_id, 'operationId': operation_id})
                self.advance(db)
                return {'id': aid, 'deleted': True}, [], False
            return self.service._operation(db, actor, 'retire', key,
                {'legacyActionDelete': aid, 'expectedRevision': expected, 'confirmReferences': confirm_references}, 'action', change)

    def owned(self, gid):
        with self.service.transaction(readonly=True) as db:
            row = db.execute("SELECT document FROM final_documents WHERE kind IN ('action_group_handoff','legacy_group_binding') AND id=?", (gid,)).fetchone()
            return bool(row and json.loads(row[0])['sourceRoot'] == self.root)

    def projection(self, state):
        state = copy.deepcopy(state)
        with self.service.transaction(readonly=True) as db:
            row = db.execute("SELECT document FROM final_documents WHERE kind='action_group_registry' AND id=?", (self.root,)).fetchone()
            if row:
                state['revision'] = json.loads(row[0])['revision']
            for row in db.execute("SELECT document FROM final_documents WHERE kind='legacy_action_binding'"):
                binding = json.loads(row[0])
                if binding['sourceRoot'] != self.root:
                    continue
                aid = binding['legacyId']
                state['actions'].pop(aid, None)
                if not binding['deleted']:
                    try:
                        state['actions'][aid] = self.action(db, binding)[0]
                    except fc.ContractError as exc:
                        state.setdefault('preservedActions', []).append({'id': aid, 'reason': exc.message})
                        from packages.jobs.final_service import definition
                        head = self.service._get(db, 'action', binding['actionId'])
                        # Preserve the unrepresentable execution descriptor,
                        # while current name/schema/lifecycle still describe
                        # the canonical head. Never advertise a new draft as
                        # enabled merely because the old source was enabled.
                        state['actions'][aid] = {**binding['raw'], 'name': head['name'], 'description': head['description'],
                            'enabled': head['enabled'] and head['state'] == 'published', 'input_schema': head['inputSchema'],
                            'effect': head['effectClass'], 'idempotent': head['retry']['idempotent']}
                        state.setdefault('canonicalActions', {})[aid] = {
                            'object': head, 'definition': definition('action', head), 'reason': exc.message}
            for row in db.execute("SELECT id,document FROM final_documents WHERE kind IN ('action_group_handoff','legacy_group_binding')"):
                gid, handoff = row[0], json.loads(row[1])
                if handoff['sourceRoot'] != self.root:
                    continue
                state['groups'].pop(gid, None)
                head = db.execute("SELECT document FROM final_documents WHERE kind='action_group' AND id=?", (gid,)).fetchone()
                if head is None:
                    continue
                head = json.loads(head[0])
                group = {'id': gid, 'name': head['name'], 'action_ids': [t['alias'] for t in head['tools']]}
                if head.get('release'):
                    release = self.service._get(db, 'action_group_release', head['release'])
                    group['build'] = {'plugin_id': 'action-group-' + gid.replace('_', '-'), 'version': release['version'],
                                      'directory': release['directory'], 'action_ids': [t['alias'] for t in release['tools']]}
                group['last_build'] = group.get('build', handoff.get('source', {}).get('last_build'))
                state['groups'][gid] = group
        return state

    def mutate(self, gid, operation, expected, key, *, actor, raw=None, create=False):
        from .groups import Groups
        adapter = Groups(self.service)
        with _lock, self.service.transaction() as db, self.service.auth.lock:
            def change(_):
                self.check_revision(db, expected)
                self.initialize(db)
                identity = gid
                if operation == 'put' and identity is None:
                    identity = 'group_' + uuid.uuid4().hex[:24]
                try:
                    group = adapter.get(db, identity)
                except KeyError:
                    group = None
                if create and group is not None:
                    fail('ID already exists; update the existing item by ID')
                if group is not None and not self.owned(identity):
                    fail('Group belongs to a different configuration authority')
                if operation == 'put':
                    value = copy.deepcopy(raw)
                    if not isinstance(value, dict) or set(value) - {'id', 'name', 'action_ids'}:
                        fail('Unknown Group fields', 'invalid_schema')
                    value.setdefault('id', identity)
                    identifier(identity)
                    if value['id'] != identity or not isinstance(value.get('action_ids'), list):
                        fail('Group identity/action_ids mismatch', 'invalid_schema')
                    if (any(not isinstance(a, str) for a in value['action_ids'])
                            or len(set(value['action_ids'])) != len(value['action_ids'])):
                        fail('Group must reference unique existing Actions')
                    tools, formats = [], {}
                    for aid in value['action_ids']:
                        try:
                            action, ref = self.release_action(db, self.binding(db, aid))
                        except KeyError:
                            fail('Action missing; no canonical identity is inferred')
                        tools.append({'alias': aid, 'actionRef': ref})
                        formats[aid] = action['output_format']
                    result = adapter.put({'id': identity, 'name': value.get('name'), 'tools': tools},
                        group['revision'] if group else 0, fc.digest(['legacy-group-put', key]),
                        actor=actor, available=False, allow_empty=True)['result']
                    saved = result['group']
                    saved['compatibility'] = {'protocol': 'legacy-flat/v1', 'outputFormats': formats}
                    # Metadata joins the same save; it is not a second revision.
                    db.execute("UPDATE final_documents SET document=? WHERE kind='action_group' AND id=?",
                               (fc.canonical_json(saved), identity))
                    if group is None:
                        Groups.save(db, 'legacy_group_binding', identity, {'sourceRoot': self.root, 'createdCanonical': True})
                        self.advance(db)
                    return {'group': saved, 'item': {**value, 'id': identity}}, [], False
                if group is None:
                    fail('Group not found')
                if operation == 'build':
                    # Old build snapshots the current Action definitions, while
                    # every already published release and running pin stays fixed.
                    tools, formats = [], {}
                    for tool in group['tools']:
                        action, ref = self.release_action(db, self.binding(db, tool['alias']))
                        tools.append({'alias': tool['alias'], 'actionRef': ref})
                        formats[tool['alias']] = action['output_format']
                    staged = {**group, 'tools': tools, 'compatibility': {'protocol': 'legacy-flat/v1', 'outputFormats': formats}}
                    db.execute("UPDATE final_documents SET document=? WHERE kind='action_group' AND id=?",
                               (fc.canonical_json(staged), identity))
                method = {'build': adapter.publish, 'unbuild': adapter.unbuild, 'delete': adapter.delete}[operation]
                result = method(identity, group['revision'], fc.digest(['legacy-group-' + operation, key]), actor=actor)
                return result['result'], [], False
            op = {'put': 'update', 'build': 'publish', 'unbuild': 'disable', 'delete': 'retire'}[operation]
            return self.service._operation(db, actor, op, key,
                {'legacyGroup': gid, 'operation': operation, 'expectedRevision': expected, 'raw': raw, 'create': create}, 'action', change)
