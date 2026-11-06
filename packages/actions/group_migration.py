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
from pathlib import Path

from packages.jobs import final_contracts as fc
from packages.jobs.runtime_owner import OwnerFence
from packages.jobs.final_runtime.ledger import principal
from .store import Store, validate_action, identifier, _lock
from .command_execution import product_definition

SOURCE_KEY = 'action-group-cooperative-source-v1'
RESERVED = {'argv', 'commands', 'cwd', 'env', 'command', 'executable', 'args',
            'timeout', 'timeout_sec', 'hideWindow', 'externalScript', 'interpreter'}


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


def command_definition(raw):
    action = validate_action(raw, automation=False)
    if (action['system'] or action['monitoring'] or action['recoverable']
            or action['effect'] == 'high_risk' or action['execution']['kind'] != 'command'):
        fail('Only ordinary legacy commands have a proven direct successor')
    if RESERVED & set(action['input_schema'].get('properties', {})):
        fail('Legacy business fields would override canonical command descriptors')
    if set(action['input_schema']) - {'type', 'properties', 'required', 'additionalProperties', 'description', 'title'}:
        fail('Complex input schema requires an explicit argument compatibility proof')
    value = product_definition('process.command')
    value.update(name=action['name'], description=action['description'], system=False, inputSchema=action['input_schema'],
                 effectClass=action['effect'], retry={**value['retry'], 'idempotent': action['idempotent']})
    value['execution']['config'] = copy.deepcopy(action['execution'])
    value['execution']['config'].pop('kind')
    return fc.validate_definition('action', value)


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
        if set(state) - {'revision', 'actions', 'groups', 'retired_plugins', 'actionRevisions', 'actionLifecycle', 'actionGenerations'}:
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
        if set(state.get('actions', {})) - referenced:
            blockers.append({'reason': 'unreferenced_actions_require_separate_writer_successor'})
        plan = {'protocol': 'legacy-group-adoption/v1', 'sourceRoot': str(self.store.root),
                'canonicalRoot': str(self.service.root.resolve()), 'profileId': self.service.profile_id,
                'expectedSourceEpoch': source_authority(self.store.root)[0],
                'sourceRevision': state['revision'], 'files': files, 'groups': groups,
                'mappings': mappings, 'blocked': blockers}
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
                        Groups.save(db, 'action_group_registry', str(self.store.root), {'revision': plan['sourceRevision']})
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
    row = db.execute("SELECT document FROM final_documents WHERE kind='action_group_handoff' AND id=?", (gid,)).fetchone()
    if row:
        root = json.loads(row[0])['sourceRoot']
        row = db.execute("SELECT document FROM final_documents WHERE kind='action_group_registry' AND id=?", (root,)).fetchone()
        if row:
            db.execute("UPDATE final_documents SET document=? WHERE kind='action_group_registry' AND id=?",
                       (fc.canonical_json({'revision': json.loads(row[0])['revision'] + 1}), root))


class LegacyGroupCompatibility:
    """Old Group shape/CAS -> the existing canonical Group operations."""
    def __init__(self, service, source_root):
        self.service, self.root = service, str(Path(source_root).resolve())

    def owned(self, gid):
        with self.service.transaction(readonly=True) as db:
            return bool(db.execute("SELECT 1 FROM final_documents WHERE kind='action_group_handoff' AND id=?", (gid,)).fetchone())

    def projection(self, state):
        state = copy.deepcopy(state)
        with self.service.transaction(readonly=True) as db:
            row = db.execute("SELECT document FROM final_documents WHERE kind='action_group_registry' AND id=?", (self.root,)).fetchone()
            if row:
                state['revision'] = json.loads(row[0])['revision']
            for row in db.execute("SELECT id,document FROM final_documents WHERE kind='action_group_handoff'"):
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
                group['last_build'] = group.get('build', handoff['source'].get('last_build'))
                state['groups'][gid] = group
        return state

    def mutate(self, gid, operation, expected, key, *, actor, raw=None):
        from .groups import Groups
        adapter = Groups(self.service)
        # Nested Group operations join this exact owner-fenced transaction.
        with self.service.transaction() as db:
            registry = self.service._get(db, 'action_group_registry', self.root)
            if expected is not None and (type(expected) is not int or expected != registry['revision']):
                fail('Legacy registry changed; reload before saving', 'revision_conflict')
            group = adapter.get(db, gid)
            if operation == 'put':
                if not isinstance(raw, dict) or set(raw) != {'id', 'name', 'action_ids'} or raw['id'] != gid:
                    fail('Unknown Group fields', 'invalid_schema')
                # Preserve aliases and fixed refs; adding unresolved legacy IDs
                # requires an explicit canonical Group edit, never alias guessing.
                refs = {t['alias']: t for t in group['tools']}
                if len(set(raw['action_ids'])) != len(raw['action_ids']) or any(a not in refs for a in raw['action_ids']):
                    fail('New legacy references need a reviewed fixed ActionRef')
                return adapter.put({'id': gid, 'name': raw['name'], 'tools': [refs[a] for a in raw['action_ids']]},
                                   group['revision'], key, actor=actor)
            method = {'build': adapter.publish, 'unbuild': adapter.unbuild, 'delete': adapter.delete}[operation]
            return method(gid, group['revision'], key, actor=actor)
