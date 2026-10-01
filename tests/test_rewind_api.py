from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.core import session as sess
from packages.web import server


class FakeHybridResult:
    def __init__(self, cli_id: str, history: list[dict]):
        self.success = True
        self.error = None
        self.forked_cli_session_id = cli_id
        self.history = history
        self.raw_usage = []
        self.truncation = None
        self.file_rewind = None


@pytest.fixture
def isolated_session_store(tmp_path, monkeypatch):
    session_dir = tmp_path / 'sessions'
    session_dir.mkdir()
    monkeypatch.setattr(sess, 'SESSION_DIR', session_dir)
    sess._cache.clear()
    sess._all_loaded = False
    monkeypatch.setattr(server, 'DATA_DIR', tmp_path / 'data')
    server._REWIND_TASKS.clear()
    yield
    server._REWIND_TASKS.clear()
    sess._cache.clear()
    sess._all_loaded = False


def test_rewind_endpoint_creates_rewound_sessions_with_unique_names(isolated_session_store, monkeypatch):
    parent = sess.Session(
        id='parent',
        name='parent',
        adapter='cbc',
        workdir='E:/tmp/pan-rewind-test',
        adapter_config={'cli_session_id': 'cli-parent'},
        history=[{'role': 'user', 'content': 'create a tracked file'}],
        workspace_ids=['ws-main'],
    )
    sess._cache[parent.id] = parent
    broadcasts: list[dict] = []
    calls: list[dict] = []

    async def fake_broadcast(payload):
        broadcasts.append(payload)

    def fake_hybrid(parent_cli, workdir, anchor, **kwargs):
        calls.append({'parent_cli': parent_cli, 'workdir': workdir, 'anchor': anchor})
        on_stage = kwargs.get('on_stage')
        if on_stage:
            on_stage('starting', {})
            on_stage('resuming', {})
        return FakeHybridResult(f'cli-fork-{len(calls)}', [dict(parent.history[0])])

    async def scenario():
        message_id = f'legacy:parent:{parent.history_epoch}:0'
        first = await server.api_rewind_session_history('parent', message_id)
        assert first['ok'] is True
        first_task = server._REWIND_TASKS[first['jobId']]
        await asyncio.wait_for(first_task, timeout=5)

        second = await server.api_rewind_session_history('parent', message_id)
        assert second['ok'] is True
        second_task = server._REWIND_TASKS[second['jobId']]
        await asyncio.wait_for(second_task, timeout=5)
        return first, second

    monkeypatch.setattr(server, 'broadcast', fake_broadcast)
    monkeypatch.setattr(server, 'run_hybrid_rewind', fake_hybrid)
    first, second = asyncio.run(scenario())

    sessions = [item for item in sess.list_all(load_history=True) if item.id != 'parent']
    assert len(sessions) == 2
    assert {item.name for item in sessions} == {'parent@0', 'parent@0-1'}
    assert {item.cli_session_id for item in sessions} == {'cli-fork-1', 'cli-fork-2'}
    # The rewound branch inherits the parent's workspace membership.
    assert all(item.workspace_ids == ['ws-main'] for item in sessions)
    assert parent.history == [{'role': 'user', 'content': 'create a tracked file'}]
    assert calls[0]['anchor'].message_text == 'create a tracked file'
    assert calls[0]['anchor'].absolute_index == 0

    progress = [item for item in broadcasts if item.get('type') == 'session.rewind.progress']
    created = [item for item in broadcasts if item.get('type') == 'session.created']
    assert any(item.get('stage') == 'completed' for item in progress)
    assert len(created) == 2
    assert any('does not track files edited manually or via bash' in item.get('limitation', '') for item in progress)

    first_record_path = server.DATA_DIR / 'rewind' / 'parent' / f'{first["jobId"]}.json'
    second_record_path = server.DATA_DIR / 'rewind' / 'parent' / f'{second["jobId"]}.json'
    first_record = json.loads(first_record_path.read_text(encoding='utf-8'))
    second_record = json.loads(second_record_path.read_text(encoding='utf-8'))
    assert first_record['status'] == 'completed'
    assert second_record['status'] == 'completed'
    assert first_record['new_pan_session_name'] == 'parent@0'
    assert second_record['new_pan_session_name'] == 'parent@0-1'


def _make_parent():
    parent = sess.Session(
        id='parent',
        name='parent',
        adapter='cbc',
        workdir='E:/tmp/pan-rewind-test',
        adapter_config={'cli_session_id': 'cli-parent'},
        history=[{'role': 'user', 'content': 'create a tracked file'}],
    )
    sess._cache[parent.id] = parent
    return parent


def test_rewind_endpoint_rejects_invalid_scope(isolated_session_store):
    parent = _make_parent()
    message_id = f'legacy:parent:{parent.history_epoch}:0'

    for bad in (0, 4, 'x', None, True, '2x'):
        result = asyncio.run(server.api_rewind_session_history('parent', message_id, {'scope': bad}))
        assert result['ok'] is False
        assert result['error']['code'] == 'invalid_scope'


def test_rewind_endpoint_passes_scope_to_hybrid(isolated_session_store, monkeypatch):
    parent = _make_parent()
    calls: list[dict] = []

    def fake_hybrid(parent_cli, workdir, anchor, **kwargs):
        calls.append({'anchor': anchor, 'scope': kwargs.get('scope')})
        return FakeHybridResult(f'cli-fork-{len(calls)}', [dict(parent.history[0])])

    async def scenario():
        message_id = f'legacy:parent:{parent.history_epoch}:0'
        results = []
        for scope in (2, '3'):
            outcome = await server.api_rewind_session_history('parent', message_id, {'scope': scope})
            assert outcome['ok'] is True
            assert outcome['scope'] == int(scope)
            await asyncio.wait_for(server._REWIND_TASKS[outcome['jobId']], timeout=5)
            results.append(outcome)
        return results

    monkeypatch.setattr(server, 'broadcast', _noop_broadcast)
    monkeypatch.setattr(server, 'run_hybrid_rewind', fake_hybrid)
    asyncio.run(scenario())

    assert [call['scope'] for call in calls] == [2, 3]


async def _noop_broadcast(payload):
    return None
