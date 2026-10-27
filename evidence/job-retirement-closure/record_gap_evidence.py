"""Annotate the nine still-open nodes without reclassifying supporting tests."""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
PREFIX = 'tests/test_jobs_api.py::'
TEST = 'tests/test_job_retirement_provider_boundaries.py::'
observations = {
    PREFIX + 'test_resume_legal_running_job_rescans_and_run_now_sends_only_to_current_candidates': {
        'kind': 'missing-native-capability',
        'blockingFact': 'No canonical Action/executor for current legal-offline candidate query and explicit resume; session_operation in dynamic executor only pauses.',
        'sharedSymbols': ['packages/jobs/final_service.py::FinalService._install_product_actions',
            'packages/actions/typed_executor.py::TypedExecutor.execute',
            'packages/actions/executor.py::execute_dynamic'],
        'minimalCoordination': 'MA must define authorized native query/resume port, persist selected original Session IDs at effect admission, and re-use legal-offline/Wake gates. Do not inject a query provider in the test.'},
    PREFIX + 'test_resume_legal_running_empty_scan_is_successful_noop': {
        'kind': 'missing-native-capability',
        'blockingFact': 'Same native resume port missing; _session_broadcast rejects empty sessionIds, so broadcast cannot prove empty dynamic selection completes.',
        'sharedSymbols': ['packages/actions/typed_executor.py::TypedExecutor._session_broadcast',
            'packages/actions/typed_executor.py::TypedExecutor.execute',
            'packages/jobs/final_service.py::FinalService._install_product_actions'],
        'minimalCoordination': 'Specify completed zero-selection receipt on native resume operation; preserve broadcast nonempty contract.'},
    PREFIX + 'test_post_scheduled_qq_action_persists_target_and_rejects_untrusted_fields': {
        'kind': 'strict-red', 'redNodes': [TEST + 'test_qq_untrusted_literal_must_be_rejected_before_committing[create]'],
        'blockingFact': 'Canonical POST accepts invalid fixed numeric QQ target and commits definition/operation (201).',
        'sharedSymbols': ['packages/jobs/final_contracts.py::_check_job_references',
            'packages/jobs/final_service.py::FinalService._references'],
        'minimalCoordination': 'Validate fully literal typed Action arguments against referenced inputSchema before write. Keep input/Event/step bindings deferred; retain immutable release and no provider call during validation.'},
    PREFIX + 'test_patch_scheduled_qq_action_is_strict_and_editable': {
        'kind': 'strict-red', 'redNodes': [TEST + 'test_qq_untrusted_literal_must_be_rejected_before_committing[update]'],
        'blockingFact': 'Canonical PUT accepts invalid fixed QQ target and commits revision 2 (200). Test fails before no-write assertion.',
        'sharedSymbols': ['packages/jobs/final_contracts.py::_check_job_references',
            'packages/jobs/final_service.py::FinalService._references'],
        'minimalCoordination': 'Same literal validation as create, using existing CAS/transaction; do not rewrite frozen historical Runs. Full original invalid bot/endpoint/text/session-target matrix still needed.'},
    PREFIX + 'test_scheduled_qq_run_now_is_targetless_and_records_plugin_failure': {
        'kind': 'partial-evidence', 'supportNodes': [TEST + 'test_channel_without_bound_plugin_records_failure_and_never_invents_time_event'],
        'blockingFact': 'Actual executor without channel provider records rejected provider_unavailable and one failed Attempt, unchanged on dispatch replay. Production has no bound channel provider. Not a real QQ plugin response.',
        'sharedSymbols': ['packages/jobs/final_service.py::FinalService.__init__',
            'packages/jobs/final_service.py::FinalService._install_product_actions',
            'packages/actions/typed_executor.py::TypedExecutor._channel'],
        'minimalCoordination': 'MA to bind real owned plugin transport to canonical channel provider with trusted bot selection; then test success/reject receipt on actual transport and manual cause, without fake provider.'},
    PREFIX + 'test_scheduled_qq_backlog_retry_runs_without_session_target': {
        'kind': 'missing-provider-retry-proof',
        'blockingFact': 'No production channel provider/receipt lookup. Product retry requires cleanup proof; cannot claim same QQ effect recovery by changing policy on a test-only Action.',
        'sharedSymbols': ['packages/jobs/final_service.py::FinalService.control_run',
            'packages/actions/typed_executor.py::TypedExecutor._channel'],
        'minimalCoordination': 'Agree versioned channel idempotency/rejection proof and original effect lookup. Test explicit rejected retry with same Run/Step/effectKey and new Attempt; unknown stays query-only. Do not restore undeliveredFires.'},
    PREFIX + 'test_post_requires_existing_session': {
        'kind': 'strict-red', 'redNodes': [TEST + 'test_missing_session_must_be_rejected_before_job_definition_commit'],
        'supportNodes': [TEST + 'test_actual_worker_provider_rejects_missing_session_without_creating_or_spawning'],
        'blockingFact': 'Missing Session fixed literal accepted at POST (201); real WorkerSessionProvider rejects at execution, creates no Session/Worker and no Session bytes change. Runtime rejection does not prove write-time rejection.',
        'sharedSymbols': ['packages/jobs/final_service.py::FinalService._references',
            'packages/actions/typed_executor.py::WorkerSessionProvider.send_session'],
        'minimalCoordination': 'MA to approve read-only literal Session existence validation before commit; do not check dynamic bindings prematurely or start Worker at validation.'},
    'tests/test_authorization_runtime.py::test_automatic_recovery_revocation_keeps_budget_and_never_starts_another_attempt': {
        'kind': 'missing-bound-recovery-policy',
        'blockingFact': 'FinalService._references always rejects automatic=true with capability_unsupported. No bound automatic policy to revoke after first Attempt; unsupported test is not a budget/revocation successor.',
        'sharedSymbols': ['packages/jobs/final_service.py::FinalService._references',
            'packages/jobs/final_service.py::FinalService._guard_effect'],
        'minimalCoordination': 'Needs separate MA design/authorization for bound policy, permission recheck and budget transaction. Do not remove unsupported guard to make test run.'},
    'tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http': {
        'kind': 'real-tcp-support-only',
        'supportNodes': ['tests/test_job_retirement_quota_transport.py::test_quota_real_tcp_cas_reopen_and_legacy_writer_fence'],
        'blockingFact': 'Real TCP CAS/auth/persistence pass; active canonical owner fences both once/monitor95 query-jobs at 409 before query. No canonical unknown query terminal. Listener reopened, not process/lifespan restart.',
        'sharedSymbols': ['packages/automation/api.py::install.quota_query_job',
            'packages/automation/quota_policy.py::Runtime.create_monitor',
            'packages/automation/quota_policy.py::Runtime.ensure_job',
            'packages/automation/quota_policy.py::Runtime._ensure_job'],
        'minimalCoordination': 'MA to migrate monitor/query admission to native Action/Job/Creator with original policy identity; preserve legacy_writer gate. Correcting old fixture NameError alone would still assert retired writer success.'},
}
mapping = json.loads((OUT / 'successors.json').read_text(encoding='utf-8'))
gaps = {r['node'] for r in mapping['originalNodes'] if r['status'] == 'gap'}
assert gaps == set(observations), (gaps ^ set(observations))
commands = {label: json.loads((OUT / (label + '-command.json')).read_text(encoding='utf-8'))
    for label in ('provider-boundaries-first', 'provider-boundaries-final', 'quota-transport', 'remaining-nine-native')}
executed = {r['node']: r['status'] for label in ('remaining-nine-native',)
    for r in commands[label]['nodes']}
for node, row in observations.items():
    for test in row.get('redNodes', []):
        assert executed[test] == 'failure', test
    for test in row.get('supportNodes', []):
        assert executed[test] == 'passed', test
    row['status'] = 'gap'
    row['closed'] = False
    row['redNodes'] = row.get('redNodes', [])
    row['supportNodes'] = row.get('supportNodes', [])
    row['evidence'] = {test: executed[test] for test in row['redNodes'] + row['supportNodes']}
    next(r for r in mapping['originalNodes'] if r['node'] == node)['currentGapReview'] = row
(OUT / 'successors.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
result = {'schema': 'pan.job-retirement-gap-evidence/v3', 'closedGapCount': 0,
    'gapCount': len(observations), 'nodes': observations, 'commands': commands,
    'sourceSha256': {relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in ('tests/job_retirement_native_fixture.py',
            'tests/test_job_retirement_provider_boundaries.py',
            'tests/test_job_retirement_quota_transport.py',
            'packages/jobs/final_service.py', 'packages/jobs/final_contracts.py',
            'packages/actions/typed_executor.py', 'packages/actions/executor.py',
            'packages/automation/api.py', 'packages/automation/quota_policy.py')},
    'artifacts': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for prefix in ('provider-boundaries-', 'quota-transport', 'remaining-nine-native')
                  for p in OUT.glob(prefix + '*') if p.is_file()},
    'limits': 'ASGI real canonical store/worker adapter and real quota TCP; no real plugin/QQ, real cohort or full lifespan acceptance.'}
(OUT / 'gap-evidence-20261028.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
lines = ['# Remaining nine gaps: 2026-10-28 exact evidence', '',
    'No gap closed. Strict reds and supporting passes are different claims.', '',
    '| Original node | Evidence and blocking fact | Minimal MA coordination |', '|---|---|---|']
for node, row in observations.items():
    lines.append('| `' + node + '` | ' + row['blockingFact'] + '<br>' + '<br>'.join(
        '`' + key + '`=' + value for key, value in row['evidence'].items()) + ' | ' +
        row['minimalCoordination'] + '<br>' + '<br>'.join('`' + s + '`' for s in row['sharedSymbols']) + ' |')
(OUT / 'remaining-nine-review-20261028.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(json.dumps({'gapCount': len(observations), 'closed': 0,
    'nativeMatrix': commands['remaining-nine-native']['counts']}))
