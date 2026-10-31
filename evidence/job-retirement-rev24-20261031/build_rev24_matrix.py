"""Versioned rev24 delta over the accepted rev23 43-node matrix."""
from pathlib import Path
import copy, json, subprocess

root = Path(__file__).resolve().parents[2]
old = root / 'evidence/job-retirement-runtime-20261031/current43-matrix.json'
matrix = json.loads(old.read_text(encoding='utf-8'))
source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
for row in matrix['originalNodes']:
    row['rev24Disposition'] = 'unchanged-from-rev23'
    row['rev24Evidence'] = []
def row(n): return next(x for x in matrix['originalNodes'] if x['ordinal'] == n)
row(16).update(rev24Disposition='visible-consumer-partially-fixed', rev24Evidence=[
    'packages/web/src/hooks/useWebSocket.ts::job.updated partial warning consumer',
    'packages/web/src/hooks/useWebSocket.partial-r24.test.ts::formats terminal target identity'],
    rev24Remaining='jsdom/fresh browser event-consumer integration unverified because declared workspace dependency @asamuzakjp/css-color is absent; server envelope remains existing job.updated/outbox summary.')
row(22).update(rev24Disposition='historical-red-preserved', rev24Evidence=[
    'tests/test_retirement_rev6_parity.py::test_real_plugin_default_selected_bot_failure_manual_receipt_and_no_replay[None-bot_not_connected]',
    'tests/test_retirement_rev6_parity.py::test_real_plugin_default_selected_bot_failure_manual_receipt_and_no_replay[222222-bot_not_connected]',
    'tests/test_retirement_rev6_parity.py::test_real_plugin_default_selected_bot_failure_manual_receipt_and_no_replay[333333-unknown_bot_uin]'],
    rev24Remaining='All three rerun red because current r10 manual retry accepts the request where the old node expected 409. Raw qq-baseline.xml/log are retained; selected/default/offline inconsistency needs exact product decision or new runtime release proof.')
row(23).update(rev24Disposition='canonical-backlog-entrypoint-gap', rev24Evidence=[
    'evidence/job-retirement-rev24-20261031/original-backlog.json',
    'tests/test_retirement_rev24_backlog.py::test_qq_known_no_send_backlog_cold_retarget_and_unknown_never_drops_note'],
    rev24Remaining='Current background_jobs._redeliver_undelivered raises RetiredEntryPoint after cc3fc708; old 9b4506 behavior is preserved only as source evidence. Canonical FinalService/CreatorRuntime has no undeliveredFires equivalent. This is the true ordinal23 product gap, not a test failure to suppress.')
row(42).update(rev24Disposition='verified-separate-monitoring-successor', rev24Evidence=[
    'tests/test_job_monitoring_completion.py::test_owned_http_detached_monitoring_retry_cold_reopen_and_actual_pulse',
    'tests/test_job_recovery_stable_reset.py::test_stable_result_denied_reason_never_resets_budget'],
    rev24Remaining='Two exact monitoring/revoke-budget successors passed; combined acceptance with the original delegated node remains MA integration evidence, not a new recovery implementation.')
row(14).update(rev24Disposition='production-runtime-opt-in-wired', rev24Evidence=[
    'tests/test_retirement_rev14_templates.py::test_production_form_explicit_runtime_assign_publishes_declaration_and_input_bindings',
    'packages/web/src/views/CanonicalJobsView.retirement-r14.test.tsx::explicitly opts the assign Template'],
    rev24Remaining='Backend and build verified; jsdom caller integration is unverified due missing declared workspace package. Shell remains ordinary process release.')
matrix['sourceSha'] = source
matrix['revision'] = 'rev24'
matrix['counts']['rev24Focused'] = {'backend': 4, 'reactPure': 1, 'build': 1}
matrix['rev24Unverified'] = [
    'QQ automatic canonical backlog mapping and current runtime channel release',
    'QQ old default/selected/offline red semantics',
    'fresh browser partial-failure toast consumer',
    'remote CI/other platforms/real migration/deployment']
(Path(__file__).parent / 'rev24-matrix.json').write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'sourceSha': source, 'originalNodeCount': len(matrix['originalNodes']), 'ordinal23': row(23)['rev24Disposition']}))
