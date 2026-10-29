"""Preserve original write-time safety on today's source-aware product releases."""
import pytest

from packages.core import session
from tests.job_retirement_native_fixture import h  # noqa: F401
from tests.test_job_retirement_literal_validation import snapshot
from tests.test_job_final_http_service import USER, create


@pytest.mark.parametrize('operation', ['create', 'update'])
@pytest.mark.parametrize('product', ['session_message', 'session_broadcast'])
def test_current_source_aware_product_missing_fixed_session_rejected_before_commit(h, product, operation):
    existing = session.create('owned valid target')
    catalog = h.service.actions(actor=USER)
    target = 'sessionIds' if product == 'session_broadcast' else 'sessionId'
    def args(sid):
        return {target: {'from': 'literal', 'value': [sid] if target == 'sessionIds' else sid},
                'text': {'from': 'literal', 'value': 'owned text'},
                'source': {'from': 'literal', 'value': 'automation'}}
    value = {'name': 'source-aware validation', 'steps': [{'stepId': 'send',
        'actionRef': catalog['references']['product_' + product + '_v2'],
        'arguments': args(existing.id), 'completion': {'contract': 'durable-queue-accepted'}}],
        'retention': {'policyRef': 'owned'}}
    item = create(h, value=value) if operation == 'update' else None
    value['steps'][0]['arguments'] = args('ses_owned_nonexistent')
    before = snapshot(h)
    response = h.client.request('PUT' if item else 'POST', '/api/jobs' + ('/' + item['id'] if item else ''),
        json={'definition': value, 'expectedRevision': 1 if item else 0, 'requestKey': 'missing-v2'})
    assert response.status_code == 400, response.text
    assert snapshot(h) == before
