"""Version original-node dispositions without overwriting old evidence.

Inherited acceptance is labelled separately from this batch's executed nodes.
Source presence/hashes audit references; they never constitute effect proof.
"""
import ast
import copy
from collections import Counter
import hashlib
import json
from pathlib import Path

out = Path(__file__).resolve().parent
root = out.parents[1]
historical_path = root / 'evidence/job-retirement-rev6-20261029/acceptance-matrix.json'
prior = json.loads(historical_path.read_text(encoding='utf-8'))
previous_commands = json.loads((out / 'commands.json').read_text(encoding='utf-8'))
commands = json.loads((out / 'rev17-commands.json').read_text(encoding='utf-8'))
executed = {e['node']: {**e, 'command': c['name'], 'sourceSha': run['sourceSha']}
            for run in [previous_commands, commands] for c in run['checks'] for e in c['nodes']}
def matching(fragment):
    return sorted(n for n in executed if fragment in n and executed[n]['status'] == 'passed')
def node(function):
    return 'tests/test_retirement_rev16_behavior.py::' + function
rows = copy.deepcopy(prior['originalNodes'])
for r in rows:
    r['priorRevision6Status'] = r['status']
    r['evidenceOrigin'] = 'historical bounded source/evidence, not rerun in revision17; not full capability acceptance'
    r['currentRedEvidence'] = []  # old red is preserved below, never discarded
    r['preservedHistoricalRedEvidence'] = prior['originalNodes'][r['ordinal'] - 1]['currentRedEvidence']
    r['priorRemaining'] = r['remaining']
def change(i, status, successors, semantics, remaining, origin='executed in revision16/17; see per-node immutable SHA'):
    r = rows[i - 1]
    r.update(status=status, strictSuccessors=successors, matchedRetainedSemantics=semantics,
             remaining=remaining, evidenceOrigin=origin)

change(6, 'verified-bounded', [node('test_creator_disable_stops_future_events_and_effects'),
    node('test_job_lifecycle_preserves_admitted_run_but_explicit_cancel_prevents_effect[disable]'),
    node('test_job_lifecycle_preserves_admitted_run_but_explicit_cancel_prevents_effect[retire]')],
    'Actual source disable prevents future Event/effect; Job disable/retire denies new admission but preserves admitted Run; explicit queued cancel has zero effect.',
    'Old flattened completed/nextFireAt fields are replaced by independent Creator/Job/Run states; not old URL compatibility.')
change(7, 'verified-bounded', [
    'tests/test_retirement_rev14_templates.py::test_production_form_assign_queue_source_frozen_identity_cold_no_duplicate',
    'tests/test_retirement_rev14_templates.py::test_fresh_production_browser_real_template_http_queue_and_shell'],
    'Accepted r14/r15 simple precomposed assign form reaches actual queue with frozen/source/task identity.',
    'Explicit simple product entry, not arbitrary unknown-action fallback or old dispatcher.', 'accepted r14/r15 evidence, not rerun')
change(16, 'product-gap', [node('test_partial_fanout_real_results_notice_receipt_once_and_cold_no_replay')],
    'Real queued/rejected per-target receipt, partial parent effect, uncertain Run; actual enqueue_notice once, both outbox receipts acked, cold no effect/notice replay.',
    'Exact red: parent WS/session summary lacks failed target/errors, only runId/status. Original visible failure detail remains lost.')
rows[15]['currentRedEvidence'] = [node('test_partial_failure_terminal_notice_identifies_actual_failed_target')]
change(18, 'verified-bounded', [
    'tests/test_retirement_rev14_templates.py::test_production_shell_form_real_process_exit_and_original_lookup',
    'tests/test_retirement_rev14_templates.py::test_fresh_production_browser_real_template_http_queue_and_shell'],
    'Accepted r15 raw quoted command/cwd/no target/passive no-Run then real owned process and cold original lookup; exact configured Windows cmd envelope.',
    'No new scheduled legacy API; unproven child COMSPEC uses original CRT; POSIX follow-up not rerun.', 'accepted r15 commands.json at 87aa0f45, not rerun')
qq_bad = matching('::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin')
qq_good = matching('::test_product_qq_legal_text_and_id_bytes_create_edit_and_frozen_plan')
for i, operation in [(19, 'create'), (20, 'update')]:
    change(i, 'verified-bounded', [n for n in qq_bad if n.endswith('-' + operation + ']')] + qq_good,
        'r7 accepted trusted fixed QQ literal validation reverified: invalid target/text/bot fields no write; legal raw text/IDs persisted, edited and frozen.',
        'No real accounts; owned plugin validation/effect boundaries separate; old retired URL is not restored.')
change(21, 'revalidation-inconsistent', matching('::test_original_plugin_real_onebot_http_success_exact_receipt_single_send')
    + matching('::test_edit_after_admission_keeps_actual_original_bot_target_text'),
    'Accepted r9/r10 real plugin HTTP routing; current default and frozen-edit checks pass, selected-bot unchanged node fails in matrix/isolated run then passes with read-only diagnostics.',
    'Current selected success is inconsistent. Preserve failing and passing raw artifacts; no inferred fix, no complete successor claimed.')
change(22, 'historical-bounded', prior['originalNodes'][21]['strictSuccessors'],
    'Targetless default/selected/unknown-selected bot has actual pre-send rejection; retains original Run/effect and no send before explicit control.',
    'Original r6 offline/error proofs retained, not rerun as these exact nodes; connection_error/unknown is not an equivalent bot-not-connected proof. No full old response alias claimed.', 'historical exact negative-path successors')
change(23, 'required-capability-gap', [],
    'Known identity-bound no-send failure -> explicit original canonical retry -> real original plugin HTTP success; same Run/Step/effect, new Attempt, one external send, duplicate control/CAS/cold replay fenced.',
    'r10 is accepted bounded MANUAL retry, not the original backlog drain lifecycle; three current unchanged retry nodes fail, one read-only diagnostic rerun passes. Automatic/no-send backlog parity requires user/MA coordination under hard parity order; unknown is never replayed.')
rows[22]['supportEvidence'].append({'node': 'tests/test_retirement_rev10_qq_retry.py::test_original_http_cold_retry_duplicate_control_cas_same_effect',
    'command': 'accepted-delta.xml and r17-qq-readonly-diagnostic.xml', 'meaning': 'bounded manual-only proof; inconsistent current revalidation, not automatic backlog successor'})
for index, fragment in [(20, 'test_original_plugin_real_onebot_http_success_exact_receipt_single_send'),
                        (22, 'test_original_http_cold_retry_duplicate_control_cas_same_effect')]:
    rows[index]['currentRedEvidence'] = [n for n, e in executed.items()
        if fragment in n and e['status'] == 'failed']
change(24, 'partial', [node('test_literal_empty_schedule_create_rejected_without_operation_creator_or_effect'),
    node('test_literal_empty_or_bad_schedule_update_rejects_without_any_write[rules0]')],
    'Canonical literal empty time rules rejected at create/update with zero operation/Creator/effect mutation.',
    'Session-message composition positive is historical support; unknown Action rejection and exact entry mapping not newly reverified. Empty-schema rejection alone does not close original multi-assertion node or clear-target backlog.')
change(25, 'verified-bounded', matching('::test_current_source_aware_product_missing_fixed_session_rejected_before_commit'),
    'Original four source-aware message/broadcast POST/PUT red nodes now reject absent fixed Session before write.',
    'r7/r8 accepted fix; no Worker/history lookup; custom Action rules remain their own schema.')
change(26, 'verified-bounded', [node('test_schedule_replacement_removes_old_rule_and_fires_only_new_real_event')],
    'Real HTTP old once -> cron+7200 interval replacement, old cursor removed, earliest new cursor, old time zero event/admission/queue, new actual Event with current revision and one queue effect.',
    'Independent Creator definition replaces old flattened schedule fields; existing published Events retain original identity.')
change(27, 'verified-bounded', [node('test_literal_empty_or_bad_schedule_update_rejects_without_any_write[rules1]')],
    'Actual malformed cron expression at HTTP update rejected without config/cursor/operation changes.',
    'Native error envelope, not old invalid_schedule code alias.')
backlog = 'tests/test_retirement_rev17_backlog.py::'
change(28, 'required-capability-gap-pending-user-decision', [],
    'Old clear-target remains enabled, accrues original dispatchKey/fireAt/text note, advances source, rereads current target/paused/enabled per reservation, removes only confirmed notes. Current dynamic-input experiment retains Event until target restoration and cold exactly-once send; it is NOT production caller parity acceptance.',
    'User has not decided target-freezing vs pending retarget boundary. Pause affected implementation/acceptance. Trigger pause currently consumes pending Event; current production clear-target mapper absent; concurrent append/retarget and complete lifecycle not proven. No disabled-draft substitution.',
    'original source 9b4506 and 68be5bd4d^ plus owned revision17 current-source observations')
rows[27]['supportEvidence'] = [{'node': backlog + name, 'command': 'r17-behavior.xml',
    'meaning': 'current-source observation only; target policy acceptance paused'} for name in [
    'test_pending_event_cold_retarget_preserves_original_identity_exactly_once',
    'test_job_disabled_pending_event_remains_then_resume_delivers',
    'test_retarget_after_event_admission_never_changes_frozen_effect',
    'test_pending_backlog_authority_revoked_no_admission_or_send']]
rows[27]['currentRedEvidence'] = [backlog + 'test_pending_event_pause_does_not_consume_original_backlog']
rows[8]['status'] = 'model-conflict-pending-user-decision'
rows[8]['remaining'] = 'Old unknown/default fallback capability cannot be treated as removed merely by D6 instance preservation. Existing explicit assign path is not arbitrary fallback. No restoration implemented; MA must seek user decision and plan update for model conflict.'
rows[41]['status'] = 'required-capability-gap'
rows[41]['remaining'] = 'Original monitoring=true/stableReset=300 full lifecycle lacks exact successor. Bounded automatic recovery acceptance is not permission to remove this capability; recovery TA owns shared scope, MA/user decision required.'
for i in range(32, 38):
    rows[i - 1]['strictSuccessors'] = [rows[i - 1]['originalNode']]
for i, suffix in [(40, 'False'), (41, 'True')]:
    rows[i - 1]['strictSuccessors'] = ['tests/test_job_retained_safety_canonical.py::test_real_owned_tree_delete_http_successor[' + suffix + ']']

source_files = {}
for r in rows:
    audits = []
    for successor in r['strictSuccessors'] + r['currentRedEvidence']:
        path, function = successor.split('::', 1)
        function = function.split('[', 1)[0]
        p = root / path
        tree = ast.parse(p.read_text(encoding='utf-8-sig'))
        assert any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function for n in tree.body), successor
        source_files[path] = hashlib.sha256(p.read_bytes()).hexdigest()
        audits.append({'node': successor, 'sourceFileSha256': source_files[path],
                       'execution': executed.get(successor, 'not rerun; inherited accepted evidence only')})
    r['successorAudit'] = audits
assert len(rows) == 43
capabilities = copy.deepcopy(prior['capabilities'])
boundaries = {
    'M01': 'r14/r15 process form/raw cmd accepted. Current actual detached process completes once; cold original outboxId reaches real Session queue once. Terminal summary loses original exit/log details and known-no-send notice deferral is a gap. Actual MCP guidance is connected but not executable old wrappers; deployment unverified.',
    'M03': 'r7/r8 source-aware fixed Session POST/PUT validation accepted and exact missing-target nodes reverified. Original durable Session provider/queue remains; retired MCP is guidance only, not a legacy writer.',
    'M04': 'Actual ordered fanout per-target results and exactly-once persisted parent notice/cold no replay verified this batch. True gap: parent terminal summary has no failed target/errors.',
    'M05': 'r14 simple assign fields -> complete passive Template -> real queue accepted. Existing scheduled Creator/Event/Trigger path retained. New replacement/lifecycle tests distinguish source disable, Job retire and Run cancel.',
    'M06': 'Original send/source/queue/manual cursor path retained; actual MCP migration guidance connected. Mandatory clear-target backlog is not fully preserved: pre-admission Event experiment works, Trigger pause loses pending fact, production caller mapping and retarget/freeze policy unresolved. No complete capability acceptance.',
    'M07': 'r14 simple command/cwd form plus r15 exact configured cmd raw-tail serialization, actual quoted process and cold original receipt accepted. Missing/unproven COMSPEC stays CRT; POSIX not rerun.',
    'M08': 'r7 valid/invalid literal QQ writes reverified. r9/r10 accepted bounded real plugin/manual retry; current matrix has four failures and read-only diagnostic two passes. Inconsistent revalidation unresolved. Original automatic known-no-send backlog lifecycle not covered by manual retry; mandatory capability gap, no silent downgrade.',
    'M11': 'r7 declared tzlocal dependency and original two timezone environment reds accepted green; no stale missing-dependency label. This batch literal empty/bad schedule rejection and real cron/interval replacement pass; no OS timezone change.',
    'M13': 'Prior CRUD/pin/retention proofs retained; real source-disable vs Job disable/retire vs queued cancel no-effect proof. Clear-target enabled backlog is mandatory; existing dynamic Event experiment is not accepted caller replacement. Trigger pause consumes pending fact. Parent partial detail and current completion notice detail/no-send deferral are gaps; target policy acceptance paused.',
    'M14': 'r14 complete zero-Creator passive Template, simple assign/shell caller and fresh production-bundle owned HTTP accepted; r15 quoted command repair accepted. Actual retired MCP names return migration guidance; no old write wrapper.',
    'M12': 'Scheduled source pause/misfire and already-created backlog are different boundaries. Current Job-disabled pending Event survives, but disabled Trigger consumes the pending fact as skipped. Old backlog reservation reread paused/enabled. Target-freezing/retarget acceptance paused for user decision; no D6 waiver.',
    'M15': 'Existing Event admits canonical Jobs and bounded quota/recovery proofs retained. Original monitoring/stable-reset full lifecycle remains a required capability gap. Preserving incompatible records under D6 does not close that functional gap; recovery TA/MA scope coordination required.',
}
for cap in capabilities:
    cap['priorRevision6Boundary'] = cap['currentBoundary']
    cap['currentBoundary'] = boundaries.get(cap['id'], cap['currentBoundary'])
    cap['priorEvidenceOrigin'] = 'historical accepted, not all rerun'
    cap['completeCapabilityAccepted'] = False
    cap['hardParityOrder'] = 'Missing old functionality requires explicit user authorization and plan update; D6 instance disposition is not a capability exemption.'
    if cap['id'] in {'M04', 'M05', 'M11', 'M13'}:
        cap['revision17Evidence'] = [e for e in executed.values() if e['command'] == 'r17-behavior']
    if cap['id'] == 'M08':
        cap['revision16Evidence'] = [e for e in executed.values() if e['command'] == 'accepted-delta']
    if cap['id'] in {'M01', 'M03', 'M05', 'M06', 'M14'}:
        cap['revision16MCP'] = 'tests/test_retirement_rev16_mcp_stdio.py::test_retired_names_actual_stdio_boundary_and_authorized_canonical_path'
matrix = {'schema': 'pan.retirement.entry-notification/revision17', 'baseline': commands['baseline'],
    'testedSourceSha': commands['sourceSha'], 'historicalMatrix': historical_path.relative_to(root).as_posix(),
    'historicalMatrixSha256': hashlib.sha256(historical_path.read_bytes()).hexdigest(),
    'originalNodeCount': len(rows), 'dispositionCounts': dict(Counter(r['status'] for r in rows)),
    'countMeaning': 'Dispositions, NOT 43 newly executed/green tests. Current command node counts are separate.',
    'originalNodes': rows, 'remainingNine': [rows[r['ordinal'] - 1] for r in prior['remainingNine']],
    'capabilities': capabilities,
    'mcpClosure': {'test': 'tests/test_retirement_rev16_mcp_stdio.py::test_retired_names_actual_stdio_boundary_and_authorized_canonical_path',
        'claim': '10 retired names absent from list; actual initialize/list/call structured guidance; no retired IPC/HTTP/operation/effect; canonical and retained paths authoritative',
        'notClaimed': 'No lossless executable legacy wrapper; unsupported is guidance, not revived capability'},
    'openProductScope': {'nodeOrdinal': 16, 'symbol': 'FinalService._settle',
        'minimum': 'Read original effect receipt target outcomes into parent outbox summary without altering Run/retry/notification identity; coordinate recovery ownership first',
        'notAuthorizedHere': ['final_service.py', 'final_contracts.py', 'coordinator', 'notification runtime']},
    'otherOpenDispositions': [9, 21, 23, 28, 42], 'checks': commands['checks'],
    'previousSourceChecks': previous_commands, 'wholeMigrationAccepted': False,
    'targetBoundaryAcceptance': 'PAUSED pending explicit user decision via MA; no shared implementation changed',
    'notificationGaps': [
        {'symbol': 'FinalService._settle', 'red': 'tests/test_retirement_rev17_process_notice.py::test_actual_detached_completion_notice_retains_original_exit_log_details', 'meaning': 'actual detached terminal receipt has exit/log; summary loses exit/log'},
        {'symbol': 'FinalService.deliver_outbox and web.server lifespan send_final_notice', 'red': backlog + 'test_known_missing_notice_target_retains_pending_then_cold_restoration_delivers', 'meaning': 'actual known local rejection mapped to unknown/uncertain; trusted no-send seam needed; no arbitrary rejected result should authorize retry'}],
    'unverified': ['deployed UI/toast consumption', 'supplier CLI consumption', 'remote CI', 'POSIX execution', 'real migration/accounts/data/deployment'],
    'sourceAuditOnlyNotEffectProof': source_files}
(out / 'current43-matrix.json').write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
table = ['# Revision17 current original43 dispositions', '',
    'Current SHA: `' + commands['sourceSha'] + '`. Accepted historical evidence and new executions are distinguished per row. No old artifacts are rewritten.', '',
    '| # | Original node | Current disposition | Exact successors / evidence origin | Remaining boundary |', '|---:|---|---|---|---|']
for r in rows:
    refs = '<br>'.join('`' + n + '`' for n in r['strictSuccessors']) or 'No strict successor'
    table.append(f"| {r['ordinal']} | `{r['originalNode']}` | {r['status']} | {refs}<br>{r['evidenceOrigin']} | {r['remaining']} |")
(out / 'node-review.md').write_text('\n'.join(table) + '\n', encoding='utf-8')
cap_table = ['# Revision17 M01–M15 capability comparison', '',
    'No row is accepted as a complete capability. Current executions and inherited source-bound evidence are separate; missing functionality requires explicit user decision and plan update.', '',
    '| ID | Original capability | Current evidence and remaining boundary |', '|---|---|---|']
for cap in capabilities:
    cap_table.append(f"| {cap['id']} | {cap['capability']} | {cap['currentBoundary']} |")
(out / 'capability-review.md').write_text('\n'.join(cap_table) + '\n', encoding='utf-8')
print(json.dumps({'originalNodes': len(rows), 'dispositions': matrix['dispositionCounts'], 'sourceAuditedFiles': len(source_files)}))
