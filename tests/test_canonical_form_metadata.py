"""Structured Action selection consumes server-issued immutable references."""
from packages.jobs.final_service import object_ref
from tests.test_job_final_http_service import h, job_def


def test_action_catalog_reference_is_actual_definition_digest_and_accepted_for_native_job(h):
    response = h.client.get('/api/jobs/actions')
    assert response.status_code == 200
    page = response.json()
    item = next(a for a in page['items'] if a['id'] == 'product_session_message')
    ref = page['references'][item['id']]
    assert ref == object_ref('action', item)
    definition = job_def(h)
    definition['steps'][0]['actionRef'] = ref
    created = h.client.post('/api/jobs', json={'definition':definition,'expectedRevision':0,'requestKey':'form-job'})
    assert created.status_code == 201, created.text
    assert created.json()['result']['object']['steps'][0]['actionRef'] == ref
    definition['steps'][0]['actionRef'] = {**ref, 'digest':'0'*64}
    rejected = h.client.post('/api/jobs', json={'definition':definition,'expectedRevision':0,'requestKey':'forged-form-job'})
    assert rejected.status_code == 409 and rejected.json()['error']['code'] == 'reference_conflict'
    assert len(h.client.get('/api/jobs').json()['items']) == 1
    assert not h.provider.calls


def test_action_reference_metadata_uses_existing_read_authorization(h):
    h.client.headers['Authorization'] = 'Bearer invalid'
    response = h.client.get('/api/jobs/actions')
    assert response.status_code == 401
    assert 'references' not in response.json()
