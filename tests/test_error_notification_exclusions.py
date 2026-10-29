"""Shared admission-only exclusions; provider facts are never prose classified."""
import json
from types import SimpleNamespace

import pytest
from packages.core import source_notifications as n
from packages.core import session_error_log
from tests.test_source_notifications import env, rows, drain, message


def event(key='error', code='usageLimitExceeded', source='codex_error_payload'):
    return {'kind': 'terminal_error', 'eventId': key, 'sessionId': 'ses_owner',
            'cli': {'errorSource': source, 'error': {'codexErrorInfo': code}}, 'errorText': 'kept error'}


@pytest.mark.parametrize('row', n.ERROR_TYPE_CATALOG, ids=lambda r: r['id'])
def test_every_catalog_type_matches_exact_structured_identity(row):
    source, code = row['id'].split('/')
    record = {'kind': code} if source == 'kind' else event(code=code, source=source)
    assert n.error_is_excluded(record, {'enabled': True, 'types': [row['id']]})
    assert not n.error_is_excluded(record, {'enabled': False, 'types': [row['id']]})


@pytest.mark.parametrize('value', [None, [], {'enabled': 'yes'}, {'types': ['unknown']}, {'types': [1]}, {'other': True}])
def test_legacy_invalid_defaults_and_strict_rejects(value):
    assert n.normalize_error_exclusions(value) == {'enabled': False, 'types': []}
    with pytest.raises(ValueError):
        n.normalize_error_exclusions(value, strict=True)


@pytest.mark.parametrize('record', [event(code='newProviderCode'), event(source='unknown'),
    {'kind': 'terminal_error', 'errorText': 'usageLimitExceeded'}, {'cli': {'errorSource': 'codex_error_payload'}}])
def test_unknown_missing_and_prose_forward_by_default(record):
    assert not n.error_is_excluded(record, {'enabled': True, 'types': [r['id'] for r in n.ERROR_TYPE_CATALOG if not r['id'].startswith('kind/')]})


@pytest.mark.parametrize('session_on,global_on', [(True, False), (False, True), (True, True)])
def test_both_origins_excluded_locker_unaffected_no_replay(env, monkeypatch, session_on, global_on):
    store, cfg = env
    cfg['newError']['enabled'] = global_on
    monkeypatch.setattr(n, 'session_policy', lambda: (session_on, 's1'))
    monkeypatch.setattr(n.session, 'get', lambda *a, **k: SimpleNamespace(readonly_session=False,
        notification_settings={'system': True, 'browser': True}, qq_report_targets=['user:123@456']))
    policy = {'enabled': True, 'types': ['codex_error_payload/usageLimitExceeded']}
    monkeypatch.setattr(n, 'error_exclusion_policy', lambda: (policy, 'epoch1'))
    n.error_event(event())
    assert rows(store) == []
    record = rows(store, 'source_notification_events')[0]
    assert record['text'] == '' and record['exclusion_epoch'] == 'epoch1'
    assert json.loads(record['targets']) == {}
    message(store)
    assert len(drain(store)) == 1
    policy['enabled'] = False
    n.error_event(event())
    assert len(rows(store)) == 1  # Locker only, not the re-observed error.


def test_admitted_targets_survive_exclusion_epoch_and_cold_reopen(env, monkeypatch):
    store, cfg = env
    policy = {'enabled': False, 'types': ['codex_error_payload/usageLimitExceeded']}
    epoch = ['before']
    monkeypatch.setattr(n, 'error_exclusion_policy', lambda: (policy, epoch[0]))
    n.error_event(event('admitted'))
    policy['enabled'] = True
    epoch[0] = 'after'
    n.error_event(event('admitted'))
    n.error_event(event('excluded'))
    reopened = type(store)(store.path)
    assert len(drain(reopened)) == 1
    n.error_event(event('admitted'))
    assert drain(reopened) == []
    assert {r['identity']: r['exclusion_epoch'] for r in rows(store, 'source_notification_events')} == {'admitted': 'before', 'excluded': 'after'}


def test_unknown_structured_error_still_admitted(env, monkeypatch):
    store, _ = env
    monkeypatch.setattr(n, 'error_exclusion_policy', lambda: ({'enabled': True, 'types': [r['id'] for r in n.ERROR_TYPE_CATALOG if not r['id'].startswith('kind/')]}, 'provider-only'))
    n.error_event(event('unknown', code='newCode'))
    assert len(drain(store)) == 1


def test_real_error_sample_remains_when_forwarding_excluded(env, monkeypatch, tmp_path):
    store, _ = env
    monkeypatch.setattr(n, 'error_exclusion_policy', lambda: ({'enabled': True, 'types': ['codex_error_payload/usageLimitExceeded']}, 'e'))
    path = tmp_path / 'error-samples.jsonl'
    session_error_log.reset_state()
    session_error_log.configure(path)
    try:
        identity = session_error_log.record_session_error(kind='terminal_error', terminal=True,
            session_id='ses_owner', task_seq=900, error_text='original history', error_text_source='pan.runtime',
            cli_raw={'error_source': 'codex_error_payload', 'error': {'codexErrorInfo': 'usageLimitExceeded', 'message': 'native error'}})
        sample, = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        assert sample['eventId'] == identity and sample['errorText'] == 'native error'
        assert rows(store) == []
        assert rows(store, 'source_notification_events')[0]['identity'] == identity
    finally:
        session_error_log.reset_state()


def test_legacy_ledger_migration_is_additive_and_idempotent(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / 'old.sqlite')
    try:
        db.execute('CREATE TABLE source_notification_events(source TEXT,identity TEXT,owner TEXT,targets TEXT,text TEXT,ref TEXT UNIQUE,PRIMARY KEY(source,identity))')
        db.execute("INSERT INTO source_notification_events VALUES('newError','old','ses_owner','{}','old text','oldref')")
        n.schema(db)
        n.schema(db)
        assert db.execute('SELECT text,exclusion_epoch FROM source_notification_events').fetchall() == [('old text', '')]
    finally:
        db.close()


def test_explicit_cli_code_and_null_code_fallback():
    assert n.error_type({'cli': {'errorSource': 'cli.error', 'errorCode': 429}}) == 'cli.error/429'
    record = event()
    record['cli']['error']['code'] = None
    assert n.error_type(record) == 'codex_error_payload/usageLimitExceeded'


def test_codex_structured_enum_tag_without_payload_classification():
    record = event(code={'responseStreamDisconnected': {'httpStatusCode': 429, 'message': 'opaque'}})
    assert n.error_type(record) == 'codex_error_payload/responseStreamDisconnected'
    record['cli']['error']['codexErrorInfo'] = {'newNativeType': {'httpStatusCode': 429}}
    assert n.error_type(record) is None
