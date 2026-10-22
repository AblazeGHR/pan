"""Atomic source registry; Groups reference Actions and publish immutable releases."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import uuid

import jsonschema

_lock = threading.RLock()
_ID = re.compile(r'[a-z][a-z0-9_]{0,31}\Z')
API_ROUTES = {'/api/sessions', '/api/models', '/api/workspaces'}
EFFECTS = {'read', 'write_local', 'write_external', 'high_risk'}


def root():
    from packages.core import session
    return session.SESSION_DIR.parent / 'actions'


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('ID must start with a lowercase letter and contain at most 32 lowercase letters, digits or underscores')
    return value


def validate_action(raw, automation=True):
    if not isinstance(raw, dict) or set(raw) - {'id', 'name', 'description', 'input_schema', 'execution', 'effect', 'enabled', 'output_format', 'idempotent'}:
        raise ValueError('Unknown Action fields')
    from .session_query import validate_action_query
    validate_action_query(raw)
    action = copy.deepcopy(raw)
    identifier(action.get('id'))
    if not isinstance(action.get('name'), str) or not action['name'].strip():
        raise ValueError('Action name is required')
    action.setdefault('description', action['name'])
    if not isinstance(action['description'], str) or not action['description'].strip():
        raise ValueError('Action description is required')
    action.setdefault('enabled', False)
    action.setdefault('idempotent', False)
    if type(action['idempotent']) is not bool:
        raise ValueError('Action idempotent must be boolean')
    action.setdefault('effect', 'read')
    action.setdefault('output_format', 'json')
    if (type(action['enabled']) is not bool or not isinstance(action['effect'], str) or action['effect'] not in EFFECTS
            or not isinstance(action['output_format'], str) or action['output_format'] not in {'json', 'text'}):
        raise ValueError('Invalid Action enabled, effect or output format')
    schema = action.get('input_schema')
    if not isinstance(schema, dict) or schema.get('type') != 'object' or schema.get('additionalProperties') is not False:
        raise ValueError('Input schema must be an object with additionalProperties false')
    # No external schema retrieval and no ambiguous reference expansion.
    def check_refs(value):
        if isinstance(value, dict):
            if any(k in value for k in ('$ref', '$dynamicRef', '$recursiveRef')):
                raise ValueError('Schema references are not supported')
            for child in value.values():
                check_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_refs(child)
    check_refs(schema)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ValueError('Invalid input schema: ' + exc.message) from exc
    for field in schema.get('properties', {}).values():
        if isinstance(field, dict) and 'default' in field:
            try:
                jsonschema.Draft202012Validator(field).validate(field['default'])
            except jsonschema.ValidationError as exc:
                raise ValueError('Invalid input default: ' + exc.message) from exc
    ex = action.get('execution')
    if not isinstance(ex, dict):
        raise ValueError('Execution is required')
    if ex.get('kind') == 'api':
        if (set(ex) != {'kind', 'method', 'path'} or ex['method'] != 'GET'
                or not isinstance(ex['path'], str) or ex['path'] not in API_ROUTES or action['effect'] != 'read'):
            raise ValueError('API Actions require a fixed allowed GET route and read effect')
    elif ex.get('kind') == 'command':
        if set(ex) - {'kind', 'executable', 'args', 'cwd', 'timeout_sec'}:
            raise ValueError('Unknown command fields')
        executable = ex.get('executable')
        if not isinstance(executable, str) or not Path(executable).is_absolute() or not Path(executable).is_file():
            raise ValueError('Command executable must be an existing absolute file')
        # A shell/script launcher defeats argv isolation, especially on Windows.
        if Path(executable).suffix.lower() in {'.bat', '.cmd', '.ps1', '.sh'} or Path(executable).stem.lower() in {'cmd', 'powershell', 'pwsh', 'bash', 'sh', 'wscript', 'cscript'}:
            raise ValueError('Shell and script launchers are not allowed')
        if not isinstance(ex.get('args'), list) or any(not isinstance(a, str) or '\x00' in a for a in ex['args']):
            raise ValueError('Command args must be a string array')
        if not isinstance(ex.get('cwd'), str) or not Path(ex['cwd']).is_absolute() or not Path(ex['cwd']).is_dir():
            raise ValueError('Command cwd must be an existing absolute directory')
        if type(ex.get('timeout_sec', 30)) is not int or not 1 <= ex.get('timeout_sec', 30) <= 30:
            raise ValueError('Command timeout must be between 1 and 30 seconds')
        properties = schema.get('properties', {})
        for arg in ex['args']:
            if '{' in arg or '}' in arg:
                match = re.fullmatch(r'\{([a-zA-Z][a-zA-Z0-9_]*)\}', arg)
                if (not match or match[1] not in properties or not isinstance(properties[match[1]], dict)
                        or properties[match[1]].get('type') not in ('string', 'number', 'integer', 'boolean')):
                    raise ValueError('Command input placeholders must occupy a complete argv element and reference a scalar field')
                if match[1] not in schema.get('required', []) and 'default' not in properties[match[1]]:
                    raise ValueError('Command placeholders require a required field or default')
    elif ex.get('kind') == 'job_template' and automation:
        if (set(ex) != {'kind', 'templateId', 'templateRevision'}
                or not isinstance(ex['templateId'], str) or not ex['templateId'].startswith('jtpl_')
                or type(ex['templateRevision']) is not int or ex['templateRevision'] < 1
                or action['effect'] != 'write_local' or schema.get('properties', {})
                or schema.get('required', [])):
            raise ValueError('Job Template Action requires a fixed template/revision and no input fields')
    elif ex.get('kind') == 'session_operation' and automation:
        if set(ex) != {'kind', 'operation', 'selector'} or ex['operation'] != 'pause_everything' or action['effect'] != 'write_local':
            raise ValueError('Invalid Session Operation')
        selector = ex['selector']
        if not isinstance(selector, dict):
            raise ValueError('Invalid Session selector')
        if selector.get('mode') == 'explicit':
            if set(selector) != {'mode', 'ids'} or not isinstance(selector['ids'], list) or any(not isinstance(i, str) or not i for i in selector['ids']):
                raise ValueError('Invalid explicit Session IDs')
        elif selector.get('mode') == 'event':
            if set(selector) != {'mode', 'path'} or not isinstance(selector['path'], str) or not selector['path']:
                raise ValueError('Invalid Event selector path')
        elif selector.get('mode') == 'query':
            if set(selector) != {'mode', 'query'}:
                raise ValueError('Query selector requires only mode and query')
            from .session_query import validate_query
            validate_query(selector['query'])
        else:
            raise ValueError('Unknown Session selector mode')
    else:
        raise ValueError('Execution kind must be api, command or session_operation')
    return action


class Store:
    def __init__(self, directory=None):
        self.root = Path(directory) if directory else root()
        self.path = self.root / 'registry.json'

    def read(self):
        if not self.path.exists():
            return {'revision': 0, 'actions': {}, 'groups': {}, 'retired_plugins': []}
        return json.loads(self.path.read_text(encoding='utf-8'))

    def mutate(self, change, expected=None, *, versioned=True):
        with _lock:
            state = self.read()
            if expected is not None and (type(expected) is not int or state['revision'] != expected):
                raise ValueError('Action registry changed; reload before saving')
            result = change(state)
            if versioned:
                state['revision'] += 1
            atomic(self.path, state)
            return result

    def put(self, kind, raw, expected=None, draft=False, create=False, authorized=False):
        def change(state):
            if kind == 'actions':
                from .session_query import validate_action_query
                validate_action_query(raw)
            value = copy.deepcopy(raw)
            if isinstance(value, dict) and 'id' not in value:
                # Allocate under the same registry writer as publication. Names
                # never determine identity; retries cannot overwrite a collision.
                for _ in range(100):
                    candidate = ('action_' if kind == 'actions' else 'group_') + uuid.uuid4().hex[:24]
                    pid = 'action-group-' + candidate.replace('_', '-')
                    if (candidate not in state['actions'] and candidate not in state['groups']
                            and pid not in state.get('retired_plugins', [])):
                        value['id'] = candidate
                        break
                else:
                    raise ValueError('Could not allocate a unique ID; retry creation')
            if create and isinstance(value, dict) and value.get('id') in state[kind]:
                raise ValueError('ID already exists; update the existing item by ID')
            item = validate_action(value) if kind == 'actions' else self.validate_group(value, state)
            if draft and kind == 'actions':
                item['enabled'] = False
            if kind == 'actions':
                old = state['actions'].get(item['id'])
                if (item['execution']['kind'] == 'job_template'
                        or (old and old['execution']['kind'] == 'job_template')):
                    if not authorized and (item['enabled'] or (old and old['enabled'])):
                        raise PermissionError('User Bearer required for delegated template Action enable/edit')
            state[kind][item['id']] = item
            if kind == 'actions':
                from packages.jobs.action_templates import check_action_graph
                # Only complete fixed capabilities can be persisted, including drafts.
                check_action_graph(state, item)
                revisions = state.setdefault('actionRevisions', {})
                revisions[item['id']] = revisions.get(item['id'], 0)+1
            return copy.deepcopy(item)
        return self.mutate(change, expected)

    def validate_group(self, raw, state):
        if not isinstance(raw, dict) or set(raw) - {'id', 'name', 'action_ids'}:
            raise ValueError('Unknown Group fields')
        gid = identifier(raw.get('id'))
        if not isinstance(raw.get('name'), str) or not raw['name'].strip():
            raise ValueError('Group name is required')
        ids = raw.get('action_ids')
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in state['actions'] for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('Group must reference unique existing Actions')
        return {**copy.deepcopy(raw), **{k: v for k, v in state['groups'].get(gid, {}).items() if k in {'build', 'last_build'}}}

    def delete(self, kind, item_id, expected=None, *, authorized=False, confirm_references=False):
        def change(state):
            item = state[kind].get(item_id)
            if not item:
                raise ValueError('Item not found')
            if kind == 'actions' and any(item_id in g['action_ids'] for g in state['groups'].values()):
                raise ValueError('Action is referenced by a Group; remove its references first')
            if kind == 'actions':
                from packages.jobs.action_templates import references
                if references(state, item_id) and (not authorized or not confirm_references):
                    raise PermissionError('User Bearer and confirmReferences required to delete referenced Action')
                generations = state.setdefault('actionGenerations', {})
                generations[item_id] = generations.get(item_id, 0)+1
                state.setdefault('actionRevisions', {})[item_id] = state.get('actionRevisions', {}).get(item_id, 0)+1
            if kind == 'groups' and item.get('build'):
                raise ValueError('Unbuild the Group before deleting it')
            del state[kind][item_id]
        self.mutate(change, expected)

    def build(self, gid, expected=None):
        def change(state):
            group = state['groups'].get(gid)
            if not group:
                raise ValueError('Group not found')
            actions = [validate_action(state['actions'][i], automation=False) for i in group['action_ids']]
            if not actions or any(not a['enabled'] for a in actions):
                raise ValueError('Build requires at least one Action and all selected Actions enabled')
            if any(a['effect'] == 'high_risk' for a in actions):
                raise ValueError('High-risk Actions require an approval policy; build is unavailable')
            pid = 'action-group-' + gid.replace('_', '-')
            version = uuid.uuid4().hex
            directory = self.root / 'builds' / gid / version
            snapshot = directory / 'snapshot.json'
            atomic(snapshot, {'group': {'id': gid, 'name': group['name']}, 'version': version, 'actions': actions})
            document = {'protocol': 'pan-capability/v1', 'plugin_id': pid,
                'provider': {'transport': 'stdio_jsonrpc', 'command_ref': pid + '-provider'},
                'capabilities': [{'id': pid + '.' + a['id'], 'export_key': a['id'],
                    'description': a['description'] + ' [effect: ' + a['effect'] + ']',
                    'contract_version': version, 'input_schema': a['input_schema'],
                    'execution': {'operation': a['id'], 'result_format': 'mcp'}} for a in actions]}
            atomic(directory / 'capabilities.json', document)
            group['build'] = {'plugin_id': pid, 'version': version,
                              'directory': str(directory.resolve()), 'action_ids': [a['id'] for a in actions]}
            group['last_build'] = copy.deepcopy(group['build'])
            state['retired_plugins'] = [p for p in state.get('retired_plugins', []) if p != pid]
            return copy.deepcopy(group['build'])
        return self.mutate(change, expected)

    def unbuild(self, gid, expected=None):
        def change(state):
            group = state['groups'].get(gid)
            if not group or not group.get('build'):
                raise ValueError('Group is not built')
            build = group.pop('build')
            state.setdefault('retired_plugins', []).append(build['plugin_id'])
        self.mutate(change, expected)


def extend_catalog(catalog):
    """Built Groups are ordinary API plugins, using all three generic projections."""
    from packages.plugins.catalog import Plugin
    for group in Store().read()['groups'].values():
        build = group.get('build')
        if not build:
            continue
        pid, directory = build['plugin_id'], Path(build['directory'])
        from packages.core.config import resolve_pan_api_url
        catalog.servers[pid + '-provider'] = {'name': pid + '-provider', 'command': sys.executable,
            'args': ['-m', 'packages.actions.provider', '--snapshot', str(directory / 'snapshot.json')],
            'cwd': str(Path(__file__).resolve().parents[2]),
            'env': {'PAN_API_URL': resolve_pan_api_url()}}
        catalog._add(Plugin(pid, group['name'], {'kind': 'pan_api', 'capabilities_file': str(directory / 'capabilities.json')}, version=build['version']))


def inactive_plugin(pid):
    if not isinstance(pid, str) or not pid.startswith('action-group-'):
        return False
    return pid in Store().read().get('retired_plugins', [])
