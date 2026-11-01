"""Compose existing public Session Action + time Creator + Trigger APIs."""
import asyncio
from datetime import datetime, timedelta, timezone
from packages.scheduler.providers import time_definition, timestamp
from tests.test_session_product_closure import config
from tests.test_job_final_creators import config as time_config
from tests.test_job_final_http_service import enabled, control


def schedule(h, operation, arguments):
    job = enabled(h, config(h, operation, **arguments))
    fire_at = timestamp(datetime.now(timezone.utc) + timedelta(seconds=60))
    creator_def = time_definition('owned Session time', time_config([
        {'ruleId':'original-once','type':'once','at':fire_at}]))
    response = h.client.post('/api/event-creators', json={'definition':creator_def,
        'expectedRevision':0,'requestKey':'owned-time'})
    assert response.status_code == 201, response.text
    creator = control(h,'event-creators',response.json()['result']['object'],'enable','enable-time').json()['result']['object']
    trigger_def = {'name':'owned Session subscription','targetJobId':job['id'],
        'creatorSelector':{'mode':'ids','ids':[creator['id']]},'eventTypes':['scheduler.time.v2']}
    response = h.client.post('/api/triggers', json={'definition':trigger_def,
        'expectedRevision':0,'requestKey':'owned-subscription'})
    assert response.status_code == 201,response.text
    control(h,'triggers',response.json()['result']['object'],'enable','enable-subscription')
    h.service.refresh_subscriptions()
    before = h.service.final.get(job['id'])
    assert not before['runs'] and not before['attempts'] and not before['effects']
    async def tick():
        await h.service.creator_runtime.tick(at=fire_at)
        await h.service.tick()
    asyncio.run(tick())
    result = h.service.final.get(job['id'])
    assert len(result['runs']) == len(result['attempts']) == len(result['effects']) == 1
    asyncio.run(tick())
    assert h.service.final.get(job['id']) == result
    return job, result
