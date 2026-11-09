"""Test-only writer for historical fixture bytes, never a runtime fallback.

These methods reproduce the retired source format solely to exercise history,
explicit identity adoption, permissions and no-replay tests on owned tmp_path.
Production Store methods must continue to refuse all definition writes.
"""
import copy
import uuid
from pathlib import Path
from packages.actions.store import Store, validate_action, atomic, _lock

class LegacySource(Store):
    def put(self, kind, raw, expected=None, draft=False, create=False, authorized=False):
        def change(state):
            if kind == 'actions':
                from packages.actions.session_query import validate_action_query
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
            if kind == 'actions' and (item.get('system') or state['actions'].get(item['id'], {}).get('system')):
                raise PermissionError('System Actions are product-managed')
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


    def set_enabled(self, aid, enabled, expected):
        if type(enabled) is not bool or type(expected) is not int:
            raise ValueError('Expected boolean enabled and integer revision')
        def change(state):
            if aid not in state['actions']:
                raise ValueError('Action missing')
            if state['actions'][aid].get('system'):
                # Definition is untouched; only a separate lifecycle override.
                state.setdefault('actionLifecycle', {})[aid] = enabled
            else:
                state['actions'][aid]['enabled'] = enabled
            revisions = state.setdefault('actionRevisions', {})
            revisions[aid] = revisions.get(aid, 0) + 1
        return self.mutate(change, expected)


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



    def mutate(self, change, expected=None, *, versioned=True):
        from packages.actions.group_migration import legacy_writer
        with _lock, legacy_writer(self.root):
            state = self.read(effective=False)
            if expected is not None and (type(expected) is not int or state['revision'] != expected):
                raise ValueError('Action registry changed; reload before saving')
            result = change(state)
            if versioned:
                state['revision'] += 1
            atomic(self.path, state)
            return result
