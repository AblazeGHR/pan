"""Exact old QQ backlog lifecycle through the retained scheduler adapter."""
import asyncio
from datetime import datetime, timedelta
import pytest

pytest_plugins = ['tests.test_jobs_api']

from packages.core import background_jobs as jobs
from packages.jobs.retired import RetiredEntryPoint
from tests.test_jobs_api import _register_unified_hooks, scheduler_store


def test_qq_known_no_send_backlog_cold_retarget_and_unknown_never_drops_note(tmp_path):
    calls = []
    mode = {'value': 'known-no-send'}

    async def send(*, target_type, target_id, text, bot_uin=None):
        calls.append((target_type, target_id, text, bot_uin))
        if mode['value'] == 'unknown':
            return {'ok': False, 'error': {'code': 'connection_error', 'message': 'ack lost'}}
        return {'ok': True, 'message_id': 'owned-backlog-1'}

    jobs.register_scheduled_tasks(qq_send=send)
    _register_unified_hooks()
    jobs.register_scheduled_tasks(qq_send=send)
    assert callable(jobs._scheduled_task_hooks['qq_send'])
    created = scheduler_store.create_task({
        'target_session_id': None, 'text': 'hello QQ',
        'action': {'api': 'send_qq', 'args': {'targetType': 'user', 'targetId': '123456', 'botUin': '987654'}},
        'schedule': {'kind': 'interval', 'interval_sec': 3600, 'anchor': datetime.now().isoformat()},
    })
    saved = scheduler_store._job_for_task(created['id'], scheduler_store.data_root())
    note = {'entryId': saved['schedule'][0]['id'],
            'fireAt': (datetime.now() - timedelta(seconds=5)).isoformat(),
            'dispatchKey': f"{saved['taskId']}:rev24", 'text': saved['text'], 'error': 'no bot'}
    saved = jobs._update(saved['jobId'], {'undeliveredFires': [note], 'targetSessionId': None},
                         registry_root=scheduler_store.data_root())
    with pytest.raises(RetiredEntryPoint) as retired:
        asyncio.run(jobs._redeliver_undelivered(saved, scheduler_store.data_root()))
    assert '_redeliver_undelivered' in str(retired.value)
    assert calls == []
    assert scheduler_store._job_for_task(saved['taskId'], scheduler_store.data_root())['undeliveredFires'] == [note]
