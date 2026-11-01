"""Record actual current receipt for a retained owned selected-bot HTTP red."""
import asyncio
import json
from tests.job_retirement_native_fixture import h
from tests.test_retirement_rev9_qq_http import qq_http, admitted


def test_selected_bot_owned_http_receipt_observation(qq_http):
    q = qq_http
    _, item, run = admitted(q, '222222', target_type='group')
    q.h.run(q.h.service.dispatch_once())
    state = q.h.service.final.get(item['id'])
    print('R17_QQ_OBSERVATION=' + json.dumps({'state': state['runs'][run['runId']]['state'],
        'externalRequests': q.rows, 'receipts': state['effects']}, ensure_ascii=True))
    assert state['runs'][run['runId']]['state'] == 'completed'
