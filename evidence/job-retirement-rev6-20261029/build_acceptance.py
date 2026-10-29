"""Versioned assertion comparison; never rewrites historical dispositions."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = 'cde7549690d3611b45b0cad3b51c982323e4f213'
OLD = json.loads((ROOT / 'evidence/job-retirement-closure/successors.json').read_text(encoding='utf-8'))
subprocess.run(['git', 'merge-base', '--is-ancestor', BASE, 'HEAD'], cwd=ROOT, check=True)
assert not subprocess.check_output(['git', 'diff', '--name-only', BASE, '--', 'packages', 'scripts',
                                   'tests/conftest.py', 'tests/support'], cwd=ROOT).strip()
commands = {p.name.removesuffix('-command.json'): json.loads(p.read_text(encoding='utf-8'))
            for p in OUT.glob('*-command.json')}
latest = {}
# Recorded execution order, independent of checkout mtimes.
order = ['session-resume','d6-source','qq-recovery','windows-public','management-time',
    'schedule-recovery-final','schedule-final','retained-read','qq-final','independent-scheduler',
    'event-broadcast','event-broadcast-final','qq-original-field-matrix','creation-source',
    'source-aware-validation','resume-native','schedule-native-final','scheduled-shell','schedule-identity-final']
assert set(order) == set(commands)
for label in order:
    for row in commands[label]['nodes']:
        latest[row['node']] = {**row, 'command':label+'-command.json'}


def selector(file, name):
    return 'tests/' + file + '.py::' + name


def proof(*selectors):
    rows = []
    for prefix in selectors:
        matches = {(r['node'], label) for label, c in commands.items() for r in c['nodes']
                   if r['status'] == 'passed' and (r['node'] == prefix or r['node'].startswith(prefix + '['))}
        assert matches, 'No actual passed node: ' + prefix
        path, name = prefix.split('::', 1)
        assert any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
                   for n in ast.walk(ast.parse((ROOT/path).read_text(encoding='utf-8-sig')))), prefix
        rows.extend({'node': node, 'command': label + '-command.json'} for node, label in sorted(matches))
    return rows


NATIVE = selector('test_retirement_rev6_resume_native', 'test_native_rescan_wake_per_fire_identity_and_empty_noop_without_provider_doubles')
SCHED = selector('test_retirement_rev6_schedule', 'test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor')
EVENT = selector('test_retirement_rev6_schedule', 'test_one_actual_time_event_independently_admits_two_original_worker_effects')
EDIT = selector('test_job_retirement_literal_validation', 'test_legal_literal_create_edit_cas_names_and_frozen_run')
QQ = selector('test_retirement_rev6_parity', 'test_real_plugin_default_selected_bot_failure_manual_receipt_and_no_replay')
QQEDIT = selector('test_retirement_rev6_parity', 'test_real_product_qq_edit_preserves_frozen_target_bot_text_and_cas')
QQINVALID = selector('test_retirement_rev6_parity', 'test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin')
V2 = selector('test_retirement_rev6_session_validation', 'test_current_source_aware_product_missing_fixed_session_rejected_before_commit')
RECOVERY = selector('test_retirement_rev6_parity', 'test_actual_failed_process_revocation_preserves_attempt_budget_without_second_effect')
SHELL = selector('test_retirement_rev6_parity', 'test_scheduled_explicit_shell_argv_preserves_command_cwd_exit_and_no_early_creation')
QUEUE = selector('test_session_product_closure', 'test_product_http_real_queue_source_and_cold_original_query')
CONFIG = selector('test_job_retirement_closure_successors', 'test_rename_description_roundtrip_cas_and_original_operation')
BLANK = selector('test_job_retirement_closure_successors', 'test_blank_name_rejection_preserves_definition_operation_and_disk')
PINS = selector('test_job_retained_canonical_pins', 'test_pin_write_failure_rolls_back_disk_and_has_no_success_or_outbox')
CURSORS = selector('test_job_final_creators', 'test_fs01_multiple_rules_and_after_publication_have_independent_cursors')
CAS = selector('test_job_final_creators', 'test_fs04_shared_reference_cas_phase_recovery_and_template_composition')
MANUAL = 'Manual admission preserves actual Creator cursor; no fake Event or legacy run-now call.'
V = 'verified-retained'
P = 'partial'
G = 'product-gap'
D = 'decision-required'
# Each entry follows the historical order only after validating unique 43 IDs.
# No inherited strict label automatically supplies current proof.
reviews = [
 (V, [selector('test_jobs_api','test_list_jobs_mixed_kinds_and_filters')], 'Mixed original records list/filter/sort via read-only fixture.', 'No legacy creation or runtime migration claim.'),
 (V, [QUEUE, SCHED], 'Canonical Job/Run/Attempt and original queue/task/source identities are linked.', 'Old kind/flat dispatch projections are internal; old MCP compatibility remains separately open.'),
 (V, [CONFIG], 'Rename/description, CAS, original operation and reload.', 'No supplier consumption claim.'),
 (V, [BLANK], 'Empty and whitespace canonical names reject before mutation.', 'Business payload names use their own schema; name scope matrix also passed.'),
 (V, [EDIT], 'Target binding edit persists revision and keeps frozen Run/plan unchanged.', 'Native definition assertion; old flat duplicate fields retired.'),
 (P, [selector('test_job_final_http_service','test_ft01_action_disable_after_admission_prevents_effect'), selector('test_job_final_http_service','test_ft01_definition_retire_preserves_already_admitted_run')], 'Action disables new effects; definition retirement retains admitted Run.', 'Old entry stop equals neither independent Creator disable nor Run cancellation; full old lifecycle combination not proved.'),
 (G, [SCHED], 'Explicit assign Template reaches actual worker.assign queue.', 'No current product default/field adapter from absent old action; do not replace with legacy-field rejection.'),
 (V, [SCHED], 'Scheduled session-message reaches real worker.send_session with canonical receipt.', 'Explicit native composition, not restored generic dispatch.'),
 (D, [], 'Original node rejects unknown public PATCH but forcibly seeds unknown action and expects fallback assign.', 'D6 requires unknown records preserved non-executable. Decide legitimate default assign separately; unsafe unknown fallback is not silently accepted or restored.'),
 (V, [NATIVE], 'Current illegal query rescan, real configured Wake, per-fire target and queue identity.', 'Paused owned Sessions; no live supplier Worker/CLI acceptance.'),
 (V, [NATIVE], 'Zero matches completes with matchedCount=0/results=[] and no new queue rows.', 'Direct native selection, not unmatched Event surrogate.'),
 (V, [selector('test_session_resume_product','test_scheduled_passive_template_matching_empty_fire_not_backlogged')], 'Matching actual time Event admits empty resume Run once, settles publication and no backlog.', 'Synthetic temporary graph, not real pending cohort.'),
 (V, [selector('test_session_resume_product','test_empty_manual_is_completed_no_effect_and_dynamic_schema_edit')], 'Edit name retains dynamic resume; fixed text/target attempts reject.', 'Configured Wake message is current source, no arbitrary replacement text.'),
 (V, [NATIVE], 'Original child effect client ID and queue ID query back to same fire Run; next fire new identity.', 'No remote provider dedup protocol invented.'),
 (V, [SCHED], MANUAL, 'Separate retained history read matrix; native fields replace old envelopes.'),
 (P, [QUEUE], 'Actual queue fanout preserves original per-target receipts/order.', 'Original partial terminal event/notification combination not re-proved by a success fanout; no mock closes this node.'),
 (V, [CURSORS, SCHED], 'Independent multi-rule cursors with real scheduled graph effects.', 'Legacy entries are mapped to Creator rule IDs; no old scheduler host execution.'),
 (P, [SHELL], 'Actual scheduled explicit cmd argv executes owned command/cwd, exit7 and cleanup; creation has no Run/effect.', 'Execution capability survives; old command-field shell adapter/simple Template absent. argv test is not full old argument compatibility.'),
 (G, [QQEDIT, QQINVALID], 'Current bound product persists QQ target/bot and validates many fields.', 'Short numeric target and whitespace text accepted at POST: exact red retained; no full create successor.'),
 (G, [QQEDIT, QQINVALID], 'Current product edit CAS preserves first frozen target/bot/text.', 'Same short target/blank text accepted at PUT revision2: exact red retained.'),
 (P, [QQ], 'Actual selected/default channel plugin errors preserved through trusted sender and canonical receipt.', 'No connected QQ account/gateway success or original success messageId effect proved.'),
 (V, [QQ], 'Targetless manual Run records real bot_not_connected/unknown_bot_uin, no time Event, no dispatch replay.', 'Offline plugin negative path only; no QQ account sends.'),
 (G, [QQ], 'Explicit failed retry is fenced 409, aggregate/Attempt count unchanged.', 'Original known backlog resend capability lacks trusted no-send proof/retry admission; unknown must not be replayed.'),
 (P, [SCHED, selector('test_job_retirement_remaining_successors','test_invalid_schedule_spec_is_rejected_before_creator_persistence')], 'Native time message composition and invalid schedule spec are verified.', 'Literal empty schedule/exact old validation envelope not equivalent to rejecting legacy kind; full boundary not claimed.'),
 (G, [EDIT, selector('test_job_retirement_provider_boundaries','test_missing_session_must_be_rejected_before_job_definition_commit')], 'v1 product write-time missing target rejection and positive edits pass.', 'Current source-aware v2 message/broadcast POST/PUT accept missing fixed Session: four exact reds.'),
 (P, [CAS, CURSORS], 'Creator reference CAS and independent cursor rule replacement primitives.', 'Exact edit from one original schedule to another with old entries removed not executed this batch.'),
 (V, [selector('test_job_retirement_remaining_successors','test_invalid_schedule_spec_is_rejected_before_creator_persistence')], 'Malformed duration fails before source/creator persistence.', 'Native schedule spec validation, not old URL compatibility.'),
 (D, [], 'Old clear-target succeeds, then parks fire as undeliverable/backlog.', 'Canonical required target rejects clear; original deferred configuration capability needs explicit disabled draft/Template decision, not generic Trigger-link test.'),
 (V, [EDIT], 'Native text edit persists and does not mutate frozen previous Run.', 'Supplier consume-after-edit not asserted.'),
 (V, [SCHED], 'Actual time fire keeps Event/Trigger IDs in canonical Run and original queue receipt.', 'Old entry identity in real migrated records remains unverified.'),
 (V, [selector('test_job_retained_pin_successor','test_retained_pin_persistence_failure_keeps_disk_and_emits_nothing'), PINS], 'Retained pin failure keeps bytes, emits nothing; native failures rollback.', 'Read-only archive and native writer proofs are separate.'),
 *[(V, [selector('test_jobs_api','test_pin_metadata_failure_does_not_fail_committed_operations')], 'Original parametrized corruption assertion through retained fixture.', 'Only read/pin semantics; fixture is not migration/effect execution proof.') for _ in range(6)],
 (V, [SCHED], MANUAL, 'Actual native scheduled assign/message and manual Runs; original real source activation not claimed.'),
 (V, [selector('test_job_retained_safety_canonical','test_terminal_child_writeback_does_not_recreate_retired_parent_successor')], 'Actual process late terminal writeback never recreates retired canonical parent.', 'No physical old cohort purge.'),
 *[(V, [selector('test_job_retained_safety_canonical','test_real_owned_tree_delete_http_successor')], 'Public cancel original owned tree then logical retire with original proof.', 'Windows current host only; not entire old owner retirement.') for _ in range(2)],
 (P, [RECOVERY], 'Actual failed detached process, revoke, unchanged original budget/deadline and no second effect.', 'Original monitoring=true/stableReset=300 unsupported by accepted bounded policy; not a complete successor of that configuration.'),
 (V, [selector('test_quota_native_admission','test_unknown_real_tcp_cold_reopen')], 'Real TCP canonical quota admission, unavailable adapter unknown, cold reopen same Run/Attempt/effect, no legacy writer.', 'No real accounts, real quota query or broader threshold/policy acceptance.'),
]
assert len(reviews) == len(OLD['originalNodes']) == 43
rows = []
for ordinal, (old, review) in enumerate(zip(OLD['originalNodes'], reviews), 1):
    status, selectors, matched, remaining = review
    path, function = old['node'].split('::', 1)
    function = function.split('[')[0]
    original = subprocess.check_output(['git', 'show', OLD['baseline'] + ':' + path], cwd=ROOT).decode('utf-8-sig')
    node = next(n for n in ast.walk(ast.parse(original)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function)
    body = ast.get_source_segment(original, node)
    actual = proof(*selectors)
    rows.append({'ordinal': ordinal, 'originalNode': old['node'], 'historicalStatus': old['status'],
        'originalSourceSha': OLD['baseline'], 'originalSourceLine': node.lineno,
        'originalSourceSha256': hashlib.sha256(body.encode()).hexdigest(), 'originalAssertions': body,
        'status': status, 'matchedRetainedSemantics': matched, 'remaining': remaining,
        'strictSuccessors': sorted({r['node'] for r in actual}) if status == V else [], 'supportEvidence': actual,
        'currentRedEvidence': [dict(command=label + '-command.json', **r) for label in
            ('qq-original-field-matrix', 'source-aware-validation') for r in commands[label]['nodes']
            if r['status'] == 'failure' and ((ordinal in (19,20) and QQINVALID in r['node'] and
                                            r['node'].endswith('-create]' if ordinal == 19 else '-update]')) or
                                           (ordinal == 25 and V2 in r['node']))]})

groups = [
 ('M01','Background process', [SHELL, selector('test_retirement_rev6_windows','test_canonical_bidirectional_file_ipc_stdout_stderr_exit7_cold_log')], 'Canonical Attempt -> default detached ExecutionHost -> original ProcessOwner; stdio/exit/cwd/public cancel proved. Old MCP creation names currently removed; compatibility open.'),
 ('M02','Independent/offline', [selector('test_job_final_independent_execution','test_fe02_real_worker_survives_the_admitting_host_process_exit'), selector('test_job_final_independent_execution','test_fe02_real_detached_worker_keeps_identity_while_pan_is_absent')], 'Actual process survives admitting host; original receipt query remains canonical. Real historical owner handoff is unverified.'),
 ('M03','Session message', [QUEUE], 'Real worker queue/source/sourceSessionId receipts; v2 fixed Session validation gap and old MCP creation compatibility remain.'),
 ('M04','Broadcast', [QUEUE, EVENT], 'Actual ordered queue fanout and Event broadcast admissions. v2 missing Session write validation and exact partial notification end-to-end remain.'),
 ('M05','Scheduled assign', [SCHED], 'Explicit passive Template -> Creator/Event/Trigger -> worker.assign proven. Absent/default old action mapping is not supplied.'),
 ('M06','Scheduled message', [SCHED], 'Real original worker send, source, queue identity and manual cursor invariance. Legacy facade/MCP compatibility open.'),
 ('M07','Shell', [SHELL], 'Command expression works through explicit cmd argv/cwd and actual scheduled process, no early spawn. Missing command-field thin adapter/Template is a user-facing compatibility gap.'),
 ('M08','QQ', [QQ, QQEDIT], 'Host QQChannelProvider -> old sender -> owned real plugin -> actual disconnected OneBot error. Short IDs/blank text, proven rejected retry, connected send success remain.'),
 ('M09','Resume', [NATIVE, selector('test_session_resume_product','test_scheduled_passive_template_matching_empty_fire_not_backlogged')], 'Real select + Wake, persisted original selection/receipt, empty matching fire. Paused isolated Sessions, no supplier consumption.'),
 ('M10','Main lifecycle', [], 'Source only this batch: protected system Action/independent supervisor remains. No real main restart/exit or original API/console full-suite timeout closure claimed.'),
 ('M11','Time source', [CURSORS], 'Shared original calendar calculators, multiple rules, decimal/DST focused nodes and explicit UTC source graphs pass. Host-zone default requires tzlocal missing in selected venv: two existing Template tests remain red.'),
 ('M12','Pause/misfire/manual', [SCHED, selector('test_job_final_creators','test_fs02_pause_advances_without_business_event_or_resume_backfill'), selector('test_scheduler_pending_adoption','test_three_breakpoints_keep_identity_and_pending_until_explicit_settlement')], 'Creator owns cursor/pause/skip; original pending synthetic identity preserved through explicit adoption. No live owner inventory or real migration.'),
 ('M13','CRUD/pin/retention/cancel', [BLANK, EDIT, PINS, selector('test_job_retained_safety_canonical','test_real_owned_tree_delete_http_successor')], 'Current HTTP/CAS/name/frozen/pin/GC/cancel and owned original cleanup. Full UI/notification-consumer and real-cohort deletion not accepted.'),
 ('M14','Template/UI', [SCHED, selector('test_scheduler_time_templates','test_shared_source_reference_checked_and_never_enabled_by_instantiation')], 'Published passive new-source graph and shared reference verified. Current UI source offers structured editors; browser not rerun, old one-form defaults/command adapter/MCP not accepted.'),
 ('M15','Trigger -> Job', [EVENT, RECOVERY, selector('test_quota_native_admission','test_unknown_real_tcp_cold_reopen')], 'One real time Event independently admits two Jobs; quota native unknown and bounded recovery effects proved. Unsupported old recovery policy preserved complex, no claim all Trigger/old hosts retired.'),
]
capabilities = [{'id': key, 'capability': title, 'currentEvidence': proof(*selectors), 'currentBoundary': boundary,
                 'completeCapabilityAccepted': False} for key,title,selectors,boundary in groups]
nine = [r for r, old in zip(rows, OLD['originalNodes']) if old['status'] == 'gap']
assert len(nine) == 9
result = {'schema': 'pan.retirement.parity/revision6', 'testedProductSha': BASE,
    'productTree': subprocess.check_output(['git','rev-parse',BASE+':packages'],cwd=ROOT,text=True).strip(),
    'historicalMappingUnchanged': True, 'originalNodeCount': len(rows), 'statusCounts': dict(Counter(r['status'] for r in rows)),
    'latestPerExecutedNodeCounts':dict(Counter(r['status'] for r in latest.values())),
    'latestPerExecutedNode':list(latest.values()),
    'executionOrder':order,
    'originalNodes': rows, 'remainingNine': [{k:v for k,v in r.items() if k != 'originalAssertions'} for r in nine],
    'capabilities': capabilities, 'commands': commands, 'wholeMigrationAccepted': False,
    'limits': ['No real profiles/accounts/51/60 datasets', 'No product/shared/CI edit', 'No remote CI invocation',
               'No deployment/browser or supplier consumption acceptance', 'Offline D6 bundle is not runtime activation'],
    'finalTestSources': {p.as_posix().removeprefix(ROOT.as_posix()+'/'): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (ROOT/'tests').glob('test_retirement_rev6*.py')}}
(OUT/'acceptance-matrix.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines = ['# Revision 6 original-node comparison', '', 'Tested product SHA: `'+BASE+'`. Historical mapping and red artifacts unchanged.', '',
 '| # | Original node | Current status | Matched semantics | Exact executed successor/support | Remaining |', '|---|---|---|---|---|---|']
for row in rows:
    refs = '<br>'.join('`'+n+'`' for n in sorted({r['node'] for r in row['supportEvidence']})) or 'Source review only'
    lines.append(f"| {row['ordinal']} | `{row['originalNode']}` | {row['status']} | {row['matchedRetainedSemantics']} | {refs} | {row['remaining']} |")
(OUT/'original-43-review.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
lines = ['# Current M01-M15 comparison', '', '| ID | Capability | Current source/effect boundary | Actual proof nodes |', '|---|---|---|---|']
for row in capabilities:
    refs = '<br>'.join('`'+n+'`' for n in sorted({r['node'] for r in row['currentEvidence']})) or 'No execution in this batch'
    lines.append(f"| {row['id']} | {row['capability']} | {row['currentBoundary']} | {refs} |")
(OUT/'m01-m15.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'originalNodes':len(rows),'nine':len(nine),'statuses':result['statusCounts'],
                  'nineStatuses':dict(Counter(r['status'] for r in nine))}))
