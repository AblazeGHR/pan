"""Real bridge protocol, with every CLI operation replaced by owned fixture state."""
import json
from pathlib import Path
from packages.cc_switch_bridge.provider import Bridge, main


def fake_run(self, args, json_output=True):
    path = Path(self.data_dir) / 'fixture-state.json'
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'current': 'p-a', 'default': 'acc-a', 'switches': 0}
    if args[:2] == ['auth', 'status']:
        result = {'default_account_id': state['default'], 'accounts': [
            {'id': 'acc-a', 'login': 'alice@example.test'}, {'id': 'acc-b', 'login': 'bob@example.test'}]}
    elif args[:2] == ['auth', 'default']:
        state['default'] = args[2]
        state['switches'] += 1
        result = None
    elif args[3] == 'list':
        result = {'currentProviderId': state['current'], 'providers': [
            {'id': 'p-a', 'name': 'OpenAI Main', 'settingsConfig': {'token': 'never-expose'}, 'apiUrl': 'https://secret@api.example.test/v1?key=secret'},
            {'id': 'p-b', 'name': 'OpenAI Secondary'}]}
    elif args[3] == 'switch':
        state['current'] = args[4]
        state['switches'] += 1
        result = None
    elif args[3] == 'quota':
        result = {'status': 'ok', 'available': True, 'queriedAt': 1792254007000,
            'result': {'type': 'subscription', 'quota': {'tiers': [
                {'name': '5 小时', 'utilization': 70, 'resetsAt': '2026-10-19T12:00:00Z'},
                {'name': '7 天', 'utilization': 30, 'resetsAt': '2026-10-23T12:00:00Z'}]}}}
    else:
        raise AssertionError('Unexpected fixture command')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding='utf-8')
    return result


Bridge._run = fake_run
main()
