"""Regression: general Pan kinds and native codes are independent matches."""
import pytest
import json
from types import SimpleNamespace
from packages.core import source_notifications as n, session_error_log
from tests.test_source_notifications import env, rows, drain


@pytest.mark.parametrize('kind', [k for k in session_error_log.KINDS if k != 'cancelled'])
def test_catalog_and_no_cli_predicate_cover_every_admitted_kind(kind):
    identity = f'kind/{kind}'
    assert identity in {row['id'] for row in n.ERROR_TYPE_CATALOG}
    assert n.error_is_excluded({'kind': kind}, {'enabled': True, 'types': [identity]})


def test_transient_codex_native_identity_and_code_are_both_selectable():
    record = {'kind': 'tool_failure', 'cli': {'errorSource': 'cli.error',
        'error': {'code': 'rate_limit', 'codexErrorInfo': {'responseStreamDisconnected': {'httpStatusCode': 429}}}}}
    assert n.error_is_excluded(record, {'enabled': True, 'types': ['cli.error/responseStreamDisconnected']})


def test_terminal_codex_rate_limit_is_not_lost_to_native_variant():
    record = {'kind': 'terminal_error', 'cli': {'errorSource': 'codex_error_payload',
        'error': {'code': 'rate_limit', 'codexErrorInfo': 'serverOverloaded'}}}
    assert n.error_is_excluded(record, {'enabled': True, 'types': ['codex_error_payload/rate_limit']})


def test_kind_and_all_specific_identities_match_independently():
    record = {'kind': 'tool_failure', 'cli': {'errorSource': 'cli.error',
        'error': {'code': 'rate_limit', 'codexErrorInfo': 'usageLimitExceeded'}}}
    assert n.error_types(record) == {'kind/tool_failure', 'cli.error/rate_limit', 'cli.error/usageLimitExceeded'}
    for identity in n.error_types(record):
        assert n.error_is_excluded(record, {'enabled': True, 'types': [identity]})
    assert not n.error_is_excluded(record, {'enabled': False, 'types': sorted(n.error_types(record))})
    assert not n.error_is_excluded(record, {'enabled': True, 'types': ['kind/worker_crash']})


def test_catalog_admission_and_old_saved_ids_share_authoritative_vocabulary():
    assert set(n.ERROR_KINDS) == set(session_error_log.KINDS) - {'cancelled'}
    assert {r['id'] for r in n.ERROR_TYPE_CATALOG if r['id'].startswith('kind/')} == {f'kind/{k}' for k in n.ERROR_KINDS}
    assert len({r['id'] for r in n.ERROR_TYPE_CATALOG}) == len(n.ERROR_TYPE_CATALOG)
    # Exact pre-fix persisted vocabulary; adding kind choices cannot discard it.
    from packages.core.runtime_issues import _CODEX_QUOTA_CODES, _CODEX_CAPACITY_CODES, _CBC_RATE_CODES
    old = ({f'codex_error_payload/{c}' for c in _CODEX_QUOTA_CODES | _CODEX_CAPACITY_CODES | {'usageLimitExceeded', 'serverOverloaded', 'responseStreamDisconnected'}}
           | {f'cli.error/{c}' for c in _CODEX_QUOTA_CODES | _CODEX_CAPACITY_CODES | _CBC_RATE_CODES | {'rate_limit'}})
    for enabled in (False, True):
        saved = {'enabled': enabled, 'types': sorted(old)}
        assert n.normalize_error_exclusions(saved, strict=True) == saved
    with pytest.raises(ValueError):
        n.normalize_error_exclusions({'enabled': True, 'types': ['kind/cancelled']}, strict=True)


@pytest.mark.parametrize('key', ['code', 'type', 'errorCode'])
def test_runtime_structured_code_aliases(key):
    assert 'cli.error/rate_limit' in n.error_types({'kind': 'transient_provider_error',
        'cli': {'errorSource': 'cli.error', 'error': {key: 'rate_limit'}}})


@pytest.mark.parametrize('with_message', [False, True])
def test_real_item_sample_retained_raw_error_is_matched(env, tmp_path, monkeypatch, with_message):
    store, _ = env
    path = tmp_path / 'sample.jsonl'
    session_error_log.reset_state()
    session_error_log.configure(path)
    monkeypatch.setattr(n, 'error_exclusion_policy', lambda: ({'enabled': True, 'types': ['cli.error/usageLimitExceeded']}, 'item'))
    error = {'codexErrorInfo': 'usageLimitExceeded'}
    if with_message:
        error['message'] = 'native tool error'
    try:
        identity = session_error_log.record_session_error(kind='tool_failure', error_text='item failed',
            error_text_source='cli.error', session_id='ses_owner', terminal=False,
            cli_raw={'type': 'codex.item_error', 'item': {'error': error}}, dedupe_token='item')
        sample = json.loads(path.read_text(encoding='utf-8').splitlines()[0])
        assert n.error_types(sample) == {'kind/tool_failure', 'cli.error/usageLimitExceeded'}
        assert sample['eventId'] == identity and rows(store) == []
        assert rows(store, 'source_notification_events')[0]['identity'] == identity
    finally:
        session_error_log.reset_state()


@pytest.mark.parametrize('kind', [k for k in session_error_log.KINDS if k != 'cancelled'])
@pytest.mark.parametrize('session_on,global_on', [(True, False), (False, True), (True, True)])
def test_kind_filter_both_origins_without_provider_code(env, monkeypatch, kind, session_on, global_on):
    store, cfg = env
    cfg['newError']['enabled'] = global_on
    monkeypatch.setattr(n, 'session_policy', lambda: (session_on, 's'))
    monkeypatch.setattr(n.session, 'get', lambda *a, **kw: SimpleNamespace(readonly_session=False,
        notification_settings={'system': True, 'browser': True}, qq_report_targets=['user:123@456']))
    policy = {'enabled': True, 'types': [f'kind/{kind}']}
    monkeypatch.setattr(n, 'error_exclusion_policy', lambda: (policy, 'e'))
    record = {'kind': kind, 'eventId': 'excluded', 'sessionId': 'ses_owner', 'errorText': 'kept history',
              'cli': {'errorSource': 'unknown', 'error': {'code': 'newProviderCode'}}}
    n.error_event(record)
    assert rows(store) == []
    assert json.loads(rows(store, 'source_notification_events')[0]['targets']) == {}
    policy['enabled'] = False
    n.error_event(record)  # Re-observation cannot replay an excluded identity.
    assert rows(store) == []
    n.error_event({**record, 'eventId': 'new'})
    assert {r['target'] for r in rows(store)} == ({'system', 'browser', 'user:123@456'} if session_on else {'user:123@456'})


@pytest.mark.parametrize('record', [{'kind': 'cancelled'}, {'kind': 'tool_failure', 'cancelled': True}])
def test_cancellation_never_admitted_even_with_known_provider_code(env, record):
    store, _ = env
    record = {**record, 'cli': {'errorSource': 'cli.error', 'error': {'code': 'rate_limit'}}}
    assert n.error_types(record) == frozenset()
    n.error_event(record)
    assert rows(store, 'source_notification_events') == []


def test_unknown_unselected_kind_and_code_never_inferred_from_prose():
    policy = {'enabled': True, 'types': [r['id'] for r in n.ERROR_TYPE_CATALOG]}
    assert not n.error_is_excluded({'kind': 'future_kind', 'errorText': 'tool_failure rate_limit'}, policy)
    assert not n.error_is_excluded({'kind': 'tool_failure', 'errorText': 'rate_limit'}, {'enabled': True, 'types': ['cli.error/rate_limit']})
    assert n.error_types({'kind': 'tool_failure', 'cli': {'errorSource': 'cli.error',
        'error': {'codexErrorInfo': {'rate_limit': {'message': 'unknown native variant'}}}}}) == {'kind/tool_failure'}


def test_kind_changes_do_not_revoke_pre_admitted_targets(env, monkeypatch):
    store, _ = env
    policy = {'enabled': False, 'types': ['kind/tool_failure']}
    monkeypatch.setattr(n, 'error_exclusion_policy', lambda: (policy, 'before' if not policy['enabled'] else 'after'))
    record = {'kind': 'tool_failure', 'sessionId': 'ses_owner', 'eventId': 'admitted', 'errorText': 'tool failed'}
    n.error_event(record)
    policy['enabled'] = True
    n.error_event({**record, 'eventId': 'excluded'})
    n.error_event(record)
    assert len(drain(store)) == 1
    assert {r['identity']: r['exclusion_epoch'] for r in rows(store, 'source_notification_events')} == {'admitted': 'before', 'excluded': 'after'}


def test_real_dsh_terminal_payload_known_code():
    from packages.core.adapters.dsh.events import TurnState, terminal_result
    state = TurnState()
    state.turn_reason = {'kind': 'error', 'error': {'message': 'missing configured credential', 'code': 'MISSING_CREDENTIAL'}}
    raw = terminal_result(state, 1)
    assert raw['error_source'] == 'dsh_turn_error'
    assert 'dsh_turn_error/MISSING_CREDENTIAL' in n.error_types({'kind': 'terminal_error',
        'cli': {'errorSource': raw['error_source'], 'error': raw['error']}})
