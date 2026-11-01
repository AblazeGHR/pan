"""Authenticated product-process admission through an owned real HTTP server."""
import sys

import httpx


class CanonicalProcessHTTP:
    def __init__(self, runtime):
        self.runtime = runtime
        key = (runtime.data_root / 'automation/user.key').read_text(encoding='utf8').strip()
        self.token = self.request('POST', '/api/automation/login', {'key': key}, authenticated=False)['token']

    def request(self, method, path, body=None, *, authenticated=True):
        headers = {'Authorization': 'Bearer ' + self.token} if authenticated else {}
        with httpx.Client(base_url=self.runtime.base_url, timeout=20, trust_env=False, verify=False) as client:
            response = client.request(method, path, json=body, headers=headers)
        assert response.status_code in {200, 201, 202}, (method, path, response.status_code, response.text)
        return response.json()

    def start(self, target, code, *, terminal=('completed', 'failed')):
        catalog = self.request('GET', '/api/jobs/actions')
        action = next(a for a in catalog['items'] if a['id'] == 'product_process_logs_v1')
        value = {'name': 'Owned terminal notification', 'steps': [{
            'stepId': 'process', 'actionRef': catalog['references'][action['id']],
            'arguments': {'argv': {'from': 'literal', 'value': [sys.executable, '-u', '-c', code]},
                          'cwd': {'from': 'literal', 'value': target['workdir']}},
            'completion': {'contract': 'process-exit'}}],
            'notification': {'on': list(terminal), 'recipients': [
                {'recipientId': 'owned-session', 'channelId': 'session', 'targetRef': target['id']}]},
            'retention': {'policyRef': 'test'}}
        job = self.request('POST', '/api/jobs', {'definition': value, 'expectedRevision': 0,
                                               'requestKey': 'owned-process-create'})['result']['object']
        job = self.request('POST', '/api/jobs/' + job['id'] + '/operations', {
            'operation': 'enable', 'expectedRevision': job['configRevision'],
            'expectedStateRevision': job['stateRevision'], 'parameters': {},
            'requestKey': 'owned-process-enable'})['result']['object']
        run = self.request('POST', '/api/jobs/' + job['id'] + '/runs', {
            'expectedRevision': job['configRevision'], 'requestKey': 'owned-process-run',
            'input': {}, 'cause': {'type': 'manual'}})
        return job, run

    def run(self, run_id):
        return self.request('GET', '/api/job-runs/' + run_id)
