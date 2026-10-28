"""Report-only source contracts, durable identity and real admission hooks."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from packages.core import source_notifications as n, config, session_error_log
from packages.interactions.store import InteractionStore, UserPrincipal
from packages.interactions.user_cards import UserCardStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    cfg = {source: {'enabled': True, 'qqReport': True, 'qqTargets': ['user:123@456'], 'epoch': 'first'} for source in n.SOURCES}
    monkeypatch.setattr(n, 'settings', lambda source: n.normalize(cfg[source]))
    monkeypatch.setattr(n.session, 'get', lambda *a, **kw: SimpleNamespace(readonly_session=False))
    from packages.interactions import api
    monkeypatch.setattr(api, 'get_store', lambda: store)
    return store, cfg


def message(store, key='one', recipient='user', body='safe content'):
    return store.accept_message('ses_owner', recipient, {'messageId': key, 'role': 'assistant',
        'content': body, 'ts': '2026-10-27T12:00:00Z'})


def rows(store, table='source_notification_attempts'):
    with store.db() as db:
        return [dict(r) for r in db.execute('SELECT * FROM ' + table)]


def drain(store, response=None, fail=False):
    calls = []
    async def sender(**kwargs):
        calls.append(kwargs)
        if fail:
            raise TimeoutError()
        return response if response is not None else {'ok': True, 'message_id': 'fake-1'}
    async def run():
        while await n.drain_once(sender, store):
            pass
    asyncio.run(run())
    return calls


def test_new_unread_identity_not_counts_read_toggle_or_restart(env):
    store, cfg = env
    row = message(store)
    assert len(drain(store)) == 1
    store.mark_message_read(UserPrincipal('user'), row['id'])
    store.mark_message_read(UserPrincipal('user'), row['id'], False)
    message(store)
    reopened = InteractionStore(store.path)
    assert drain(reopened) == []
    assert rows(store)[0]['state'] == 'sent'
    message(store, 'two')
    assert len(drain(store)) == 1


def test_off_enable_never_backfills_and_sources_independent(env):
    store, cfg = env
    cfg['lockerUnread']['enabled'] = False
    message(store)
    cfg['lockerUnread']['enabled'] = True
    cfg['lockerUnread']['epoch'] = 'second'
    message(store)
    assert drain(store) == []
    message(store, 'two')
    assert len(drain(store)) == 1


def test_off_on_cancels_pending_epoch(env):
    store, cfg = env
    message(store)
    cfg['lockerUnread']['epoch'] = 'reopened'
    assert drain(store) == []
    assert rows(store)[0]['state'] == 'cancelled'
    message(store, 'new')
    assert len(drain(store)) == 1


@pytest.mark.parametrize('change', ['disabled', 'report_off', 'removed', 'locked', 'missing'])
def test_claim_rechecks_policy_and_source_permission(env, monkeypatch, change):
    store, cfg = env
    message(store)
    if change == 'disabled': cfg['lockerUnread']['enabled'] = False
    if change == 'report_off': cfg['lockerUnread']['qqReport'] = False
    if change == 'removed': cfg['lockerUnread']['qqTargets'] = []
    if change in ('locked', 'missing'):
        monkeypatch.setattr(n.session, 'get', lambda *a, **kw: None if change == 'missing' else SimpleNamespace(readonly_session=True))
    assert drain(store) == []
    assert rows(store)[0]['state'] == 'cancelled'


def test_other_recipient_and_locked_admission_store_no_content(env, monkeypatch):
    store, cfg = env
    message(store, recipient='other')
    monkeypatch.setattr(n.session, 'get', lambda *a, **kw: SimpleNamespace(readonly_session=True))
    message(store, 'locked')
    assert drain(store) == []
    assert all(not r['text'] and json.loads(r['targets']) == {} for r in rows(store, 'source_notification_events'))


@pytest.mark.parametrize('response,state', [({'ok': True, 'delivery': 'queued'}, 'queued'), ({'ok': True, 'queued': True}, 'queued'),
    ({'ok': True, 'delivery': {'state': 'unknown'}}, 'unknown'),
    ({'ok': False, 'error': {'code': 'connection_error'}}, 'unknown'),
    ({'ok': False, 'error': {'code': 'rejected'}}, 'failed')])
def test_receipts_not_delivery_and_no_retry(env, response, state):
    store, _ = env
    message(store)
    assert len(drain(store, response)) == 1
    assert rows(store)[0]['state'] == state
    assert drain(InteractionStore(store.path)) == []


def test_sender_exception_unknown_does_not_break_content(env):
    store, _ = env
    row = message(store)
    assert len(drain(store, fail=True)) == 1
    assert rows(store)[0]['state'] == 'unknown'
    assert store.get_message(UserPrincipal('user'), row['id'])
    assert drain(store) == []


def test_crash_after_claim_is_not_retried(env):
    store, _ = env
    message(store)
    assert n._claim(store)
    assert rows(store)[0]['state'] == 'attempted'
    assert drain(InteractionStore(store.path)) == []


def test_concurrent_claim_only_one_attempt(env):
    store, _ = env
    message(store)
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(lambda _: n._claim(store), range(2)))
    assert sum(item is not None for item in claimed) == 1


def test_rollback_cannot_emit_and_notification_failure_cannot_reject_content(env, monkeypatch):
    store, _ = env
    with pytest.raises(RuntimeError):
        with store.db() as db:
            n.stage(db, 'lockerUnread', 'rolled-back', 'ses_owner', 'private')
            raise RuntimeError()
    assert rows(store) == []
    monkeypatch.setattr(n, 'settings', lambda *_: (_ for _ in ()).throw(OSError('unavailable')))
    row = message(store)
    assert store.get_message(UserPrincipal('user'), row['id'])
    assert rows(store) == []


def test_summary_redacted_bounded_plain_text_and_routing(env):
    store, cfg = env
    message(store, body='password=private [CQ:at,qq=all] ' + 'x' * 5000)
    call, = drain(store)
    assert 'private' not in call['text'] and '[CQ:at' not in call['text']
    assert 'ses_owner' in call['text'] and 'lmsg_' in call['text']
    assert len(call['text']) <= 1200
    assert call['target_type'] == 'private' and call['target_id'] == '123' and call['bot_uin'] == '456'


def test_cards_and_answer_hooks_only_new_unread(env):
    store, _ = env
    request = {'ownerSessionId': 'ses_owner', 'recipientId': 'user', 'requestKey': 'new',
               'card': {'title': 'Question', 'body': 'Hidden full body', 'summaryBody': 'Safe summary'}}
    store.create(request)
    store.create(request)
    cards = UserCardStore(store)
    card = cards.create(UserPrincipal('user'), {'ownerSessionId': 'ses_owner', 'requestKey': 'user-card',
        'kind': 'question', 'body': 'User question', 'attachments': []})
    assert len(rows(store)) == 1
    cards.answer('ses_owner', {'cardId': card['id'], 'body': 'New answer', 'expectedRevision': 0})
    calls = drain(store)
    assert len(calls) == 2
    assert 'Hidden full body' not in str(calls) and 'Safe summary' in str(calls)


def test_authoritative_errors_dedupe_cancellation_and_no_raw_history(env, tmp_path):
    store, cfg = env
    session_error_log.reset_state()
    session_error_log.configure(tmp_path / 'errors.jsonl')
    kwargs = dict(kind='terminal_error', error_text='api_key=secretvalue failed', error_text_source='pan',
        terminal=True, session_id='ses_owner', task_seq=8, previous_message={'text': 'SECRET HISTORY'},
        previous_captured=True, cli_raw={'secret': 'RAW SECRET'})
    first = session_error_log.record_session_error(**kwargs)
    assert session_error_log.record_session_error(**kwargs) == first
    session_error_log.record_session_error(**{**kwargs, 'kind': 'cancelled', 'cancelled': True})
    calls = drain(store)
    assert len(calls) == 1
    assert first in calls[0]['text']
    assert all(secret not in calls[0]['text'] for secret in ['secretvalue', 'SECRET HISTORY', 'RAW SECRET'])
    session_error_log.reset_state()
    assert session_error_log.record_session_error(**kwargs) == first
    assert drain(store) == []
    session_error_log.reset_state()


@pytest.mark.parametrize('value', [{'enabled': 'yes'}, {'qqSubscribe': True}, {'qqTargets': ['bad']},
    {'enabled': True, 'qqReport': True}, {'system': 'yes'}, []])
def test_strict_source_settings_report_only(value):
    with pytest.raises(ValueError): n.normalize(value, strict=True)


@pytest.mark.parametrize('session_on,global_on', [(False, False), (True, False), (False, True), (True, True)])
def test_session_global_union_system_once_and_qq_same_path(env, monkeypatch, session_on, global_on):
    store, cfg = env
    from packages.core import notifications
    fake = SimpleNamespace(readonly_session=False, notification_settings={'system': True, 'browser': True},
                           qq_report_targets={'user:123@456'})
    monkeypatch.setattr(n.session, 'get', lambda *a, **kw: fake)
    monkeypatch.setattr(n, 'session_policy', lambda: (session_on, 'policy-1'))
    cfg['newError'].update(enabled=global_on, system=True, browser=True)
    sent_system, browser, qq = [], [], []
    monkeypatch.setattr(notifications, '_system_sender', lambda title, body: sent_system.append((title, body)) or {'ok': True})
    async def send(**kwargs):
        qq.append(kwargs)
        return {'ok': True}
    async def bcast(payload): browser.append(payload)
    record = {'eventId': 'se_same-error', 'sessionId': 'ses_owner', 'kind': 'terminal_error', 'errorText': 'failure'}
    n.error_event(record)
    n.error_event(record)
    async def run():
        while await n.drain_once(send, store, browser_sender=bcast): pass
    asyncio.run(run())
    expected = int(session_on or global_on)
    assert len(sent_system) == len(qq) == len(browser) == expected
    assert drain(InteractionStore(store.path)) == []
    if expected:
        assert set(browser[0]) == {'type', 'notificationId'}
        event = n.browser_event(store, browser[0]['notificationId'])
        assert event['body'].endswith('failure')


def test_route_change_preserves_other_origin_and_does_not_add_targets(env, monkeypatch):
    store, cfg = env
    fake = SimpleNamespace(readonly_session=False, notification_settings={}, qq_report_targets={'user:123@456', 'group:222@456'})
    monkeypatch.setattr(n.session, 'get', lambda *a, **kw: fake)
    monkeypatch.setattr(n, 'session_policy', lambda: (True, 'policy-1'))
    record = {'eventId': 'se_origin', 'sessionId': 'ses_owner', 'kind': 'terminal_error'}
    n.error_event(record)
    n.invalidate_session('ses_owner')
    # Global target survives Session disconnection; Session-only one retires.
    assert len(drain(store)) == 1
    assert {r['target']: r['state'] for r in rows(store)} == {'user:123@456': 'sent', 'group:222@456': 'cancelled'}
    cfg['newError']['qqTargets'].append('user:999')
    n.error_event(record)
    assert len(rows(store)) == 2 and drain(store) == []


def test_behavior_off_on_never_revives_old_session_error(env, monkeypatch):
    store, cfg = env
    cfg['newError']['enabled'] = False
    fake = SimpleNamespace(readonly_session=False, notification_settings={}, qq_report_targets={'user:123@456'})
    monkeypatch.setattr(n.session, 'get', lambda *a, **kw: fake)
    monkeypatch.setattr(n, 'session_policy', lambda: (True, 'original'))
    n.error_event({'eventId': 'se_old', 'sessionId': 'ses_owner', 'kind': 'terminal_error'})
    monkeypatch.setattr(n, 'session_policy', lambda: (True, 'reopened'))
    assert drain(store) == []
    n.error_event({'eventId': 'se_new', 'sessionId': 'ses_owner', 'kind': 'terminal_error'})
    assert len(drain(store)) == 1


def test_notification_admission_exception_does_not_prevent_error_sample(env, monkeypatch, tmp_path):
    session_error_log.reset_state()
    session_error_log.configure(tmp_path / 'safe-error.jsonl')
    monkeypatch.setattr(n, 'error_event', lambda *_: (_ for _ in ()).throw(OSError('admission unavailable')))
    eid = session_error_log.record_session_error(kind='terminal_error', terminal=True,
        error_text='real error', error_text_source='pan', session_id='ses_owner')
    assert eid is not None
    assert json.loads(session_error_log.path().read_text(encoding='utf-8'))['eventId'] == eid
    session_error_log.reset_state()


def test_connection_change_does_not_retire_newly_admitted_grant(env, monkeypatch):
    store, cfg = env
    cfg['newError']['enabled'] = False
    fake = SimpleNamespace(readonly_session=False, notification_settings={}, qq_report_targets={'user:123@456'})
    monkeypatch.setattr(n.session, 'get', lambda *a, **kw: fake)
    monkeypatch.setattr(n, 'session_policy', lambda: (True, 'policy-1'))
    previous = n.routes('newError', 'ses_owner')
    n.error_event({'eventId': 'se_before-change', 'sessionId': 'ses_owner', 'kind': 'terminal_error'})
    fake.qq_report_targets.add('group:222@456')
    n.error_event({'eventId': 'se_after-change', 'sessionId': 'ses_owner', 'kind': 'terminal_error'})
    n.invalidate_session('ses_owner', previous)
    calls = drain(store)
    assert len(calls) == 2
    assert all('se_after-change' in call['text'] for call in calls)


def test_structured_product_error_without_session_uses_only_global_policy(env, monkeypatch):
    store, cfg = env
    monkeypatch.setattr(n.session, 'get', lambda *a, **kw: None)
    n.error_event({'eventId': 'se_product_without_session', 'sessionId': None, 'kind': 'terminal_error', 'errorText': 'Product failure'})
    calls = drain(store)
    assert len(calls) == 1 and 'se_product_without_session' in calls[0]['text']
