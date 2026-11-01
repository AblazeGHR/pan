"""Rebuild conservative original-node and E/S/U review; never modifies old evidence."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
prior = json.loads((ROOT / 'evidence/r6i-repair/original-red-assessment.json').read_text(encoding='utf-8'))
nodes = []
for old in prior['nodes']:
    row = dict(old)
    category = old['category']
    row.update(status='gap', strictSuccessors=[], boundary=old['followup'])
    if category in ('history-fixture', 'retained-metadata-gap'):
        row.update(status='strict-successor', strictSuccessors=[old['node']],
            boundary='P24 offline historical fixture; no canonical WS CRUD or executable legacy writer claim')
    elif category == 'stale-injection':
        row.update(status='strict-successor', strictSuccessors=[
            'tests/test_job_retained_pin_successor.py::test_retained_pin_persistence_failure_keeps_disk_and_emits_nothing'])
    elif category == 'retained-bridge-gap':
        row.update(status='strict-successor', strictSuccessors=[
            'tests/test_job_retained_history_bridge.py::test_run_now_does_not_move_periodic_next_fire_retained_successor'],
            boundary='P24 synthetic same-identity owner/cursor/receipt; real cohorts remain unverified')
    elif category == 'retained-safety-gap':
        name = ('test_terminal_child_writeback_does_not_recreate_retired_parent_successor'
            if 'child_writeback' in old['node'] else
            'test_real_owned_tree_delete_http_successor' + old['node'][old['node'].index('['):])
        row.update(status='strict-successor', strictSuccessors=[
            'tests/test_job_retained_safety_canonical.py::' + name],
            boundary='P23 public Run cancel then logical retire; no physical definition purge claim')
    suffix = old['node'].split('::')[-1]
    replacements = {
        'test_patch_rename_and_description': 'test_rename_description_roundtrip_cas_and_original_operation',
        'test_patch_target_switch_updates_flat_and_struct': 'test_target_and_text_edit_only_changes_future_frozen_runs',
        'test_patch_text_updates_dispatch_body': 'test_target_and_text_edit_only_changes_future_frozen_runs',
    }
    if suffix in replacements:
        row.update(status='strict-successor', strictSuccessors=[
            'tests/test_job_retirement_closure_successors.py::' + replacements[suffix]],
            boundary='Native definition/CAS/frozen Run with recording Session provider; no real CLI consumption')
    if suffix == 'test_patch_blank_name_rejected':
        row.update(status='product-gap', strictSuccessors=[
            'tests/test_job_retirement_closure_successors.py::test_blank_name_rejection_preserves_definition_operation_and_disk'],
            boundary='Empty string rejected; whitespace-only values accepted and committed: strict successor red')
    nodes.append(row)
for node, boundary in [
    ('tests/test_authorization_runtime.py::test_automatic_recovery_revocation_keeps_budget_and_never_starts_another_attempt',
     'Unbound automatic recovery is unsupported; full bound policy revocation/budget successor missing'),
    ('tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http',
     'Original NameError before HTTP; canonical query/control real TCP successor missing; do not restore retired Job POST')]:
    nodes.append(dict(node=node, category='additional-original-red', status='gap',
        strictSuccessors=[], boundary=boundary))

plan = (ROOT / 'docs/design/job-unification/R5_R6_EXECUTION_PLAN_20261026.md').read_text(encoding='utf-8')
items = []
for line in plan.splitlines():
    if not re.match(r'\| [ESU]\d\d', line):
        continue
    columns = line.split('|')
    for identity in columns[1].strip().split('/'):
        items.append(dict(id=identity, originalScope=columns[2].strip(),
            requirement=columns[3].strip(), status='partial-blocked',
            currentEvidence='inventory.json dependencyEdges/surfaceCandidates; focused.xml behavioral nodes',
            remaining='G2/G3 identity facts and G4 complete capability/transport acceptance absent; no host deletion'))
assert len(items) == 45 and len({r['id'] for r in items}) == 45
assert len(nodes) == len({r['node'] for r in nodes}) == 43
for row in items:
    if row['id'] in {'S04', 'S06', 'U08', 'U09'}:
        row['currentEvidence'] += '; P24 bridge/history and scheduler dependency/startup/source migration tests'
        row['remaining'] = 'store/time_events/legacy_bridge intentionally retained; synthetic owner evidence is not real cohort settlement'
    if row['id'] in {'S13', 'U12'}:
        row['currentEvidence'] += '; P23 public cleanup; P24 canonical pin repair and history metadata'
        row['remaining'] = 'Full Settings/retention/handoff management, real sender/WS/CLI consumption not established'
result = dict(schema='pan.job-retirement-successors/v2', baseline='add4bef7773216dd79793733a50a2ec19a6869c3',
    originalNodes=nodes, retirementItems=items,
    p23OriginalThree='Subset of originalNodes, not three additional red nodes',
    claim='Strict successors are scoped semantic evidence; never whole migration or deletion acceptance')
reviews = {
    'E01': ('server.py retains read/cancel; retired write wrappers coexist with canonical API', 'test_job_final_retirement.py', 'G2 old Runner query/cancel ownership must remain'),
    'E02': ('Legacy session-message producer removed/refused; native typed message remains', 'test_job_final_retirement.py', 'Real Session/QQ provider-specific exact successors remain incomplete'),
    'E03': ('Legacy identity must remain audit-only; canonical User/Agent scope separate', 'test_job_final_http_service.py', 'Do not elevate legacy history owner to trusted caller'),
    'E04': ('jobs/api.py remains imported facade/history; final_api owns native definitions', 'test_scheduler_surface_retirement.py', 'Cannot delete mixed facade before history/control parity'),
    'E05': ('Old template writes refused; native passive Template supported', 'test_job_final_http_service.py', 'Full custom/builtin provenance migration not established'),
    'E06': ('Scheduler read router remains; old writes 410; owner-switch absent', 'test_scheduler_surface_retirement.py', 'History/read authorization and real owner settlement required before router removal'),
    'E07': ('background start/retry absent from catalog, read/cancel retained', 'test_job_final_retirement.py', 'Body/registry de-registration is not full producer file retirement'),
    'E08': ('message create/update absent from catalog, read/cancel retained', 'test_job_final_retirement.py', 'Real existing Runner identities not inspected'),
    'E09': ('Six scheduler write bodies absent; list/get/runs retained', 'test_scheduler_surface_retirement.py', 'Read MCP tools intentionally remain; full nine-tool removal not true'),
    'E10': ('server.py still contains create_service_job/transition_service_job lifecycle compatibility', 'test_job_final_http_service.py', 'Protected lifecycle receipt handoff and old owner inventory incomplete'),
    'E11': ('React canonical management inherited; this task does not modify or rerun UI', 'R6_I_REPAIR_20261027.md', 'Current full management/pin dual-source UI acceptance absent; CP-X3 scope untouched'),
    'E12': ('Creator source client inherited; scheduler dynamic catalog tested', 'test_scheduler_surface_retirement.py', 'No browser rebuild/retest this round; no real source migration claim'),
    'E13': ('Native PUT/operation editing works; whitespace names commit unexpectedly', 'test_job_retirement_closure_successors.py', 'Two new strict reds; complete control/pin/retention/handoff UI not accepted'),
    'E14': ('CLI/console/setup scripts remain entrypoints; lifecycle helpers retain old registry', 'inventory.json surfaceCandidates', 'No lifecycle restart/exit executed; no claim of full client cutover'),
    'E15': ('independent_service still hosts JobStore/ServiceLock and canonical /executions', 'test_job_retained_safety_canonical.py', 'Keep canonical executor/cleanup/query; old cohort read/cancel/ack clearance missing'),
    'S01': ('29 import edges to background_jobs remain across packages/scripts', 'inventory.json dependencyEdges', 'Store/path/history/locks consumers must be separated before file deletion'),
    'S02': ('Canonical source cold recovery runs same identity; legacy pass does not tick injected runtime', 'test_scheduler_source_migration.py; test_scheduler_dependency_cutover.py', 'Unknown missing commit stays uncertain; no real activation inventory'),
    'S03': ('P23 late receipt leaves retired parent unchanged; P24 historical IDs retained', 'test_job_retained_safety_canonical.py; test_jobs_api.py', 'Real hidden-child receipt/owner cohort settlement absent'),
    'S04': ('store.py retained, no package host import in current dependency inventory', 'test_scheduler_dependency_cutover.py', 'Offline migration tooling/rollback archive and real history transfer still required'),
    'S05': ('scheduler engine removed by inherited work; source engine semantics moved canonical', 'test_scheduler_dependency_cutover.py', 'Removal of engine alone does not establish all hosts retirement'),
    'S06': ('legacy_bridge.py retained; P24 synthetic manual Run preserves cursor/receipt', 'test_job_retained_history_bridge.py', 'Real original switch operations/owner/receipt finality unknown'),
    'S07': ('system_actions retains execute_typed and legacy helpers guarded by owner contract', 'test_job_final_http_service.py', 'Immutable lifecycle authority and bound automatic recovery parity incomplete'),
    'S08': ('18 imports of action_runtime remain; canonical FinalService drives native runs', 'inventory.json dependencyEdges; test_scheduler_source_migration.py', 'Cannot remove whole Runtime while facade/history/authorization helpers depend on it'),
    'S09': ('Automation Trigger admission remains a required canonical consumer', 'test_scheduler_source_migration.py', 'Matching/admission/store are not leaf execution hosts; preserve'),
    'S10': ('main_lifecycle canonical and legacy registry compatibility remain', 'inventory.json surfaceCandidates', 'No cross-service lifecycle receipt/owner settlement proof this round'),
    'S11': ('P24 outbox pending/ack failure original key and real queue persistence pass', 'test_job_retained_canonical_pins.py', 'Production send_final_notice WS/CLI actual consumption absent'),
    'S12': ('independent_runner remains for ServiceLock/compat; native executor lives separately', 'test_job_retained_safety_canonical.py', 'Do not delete canonical /executions with old /jobs lifecycle; old owner clearance absent'),
    'S13': ('P24 config/runtime pin holds both protect delete/GC; P23 public owned cleanup passes', 'test_job_retained_canonical_pin_resolution.py; test_job_retained_safety_canonical.py', 'Settings/retention/handoff complete management and transport evidence missing'),
    'S14': ('P23 original PID/FILETIME/tree proof succeeds using specified venv', 'test_job_retained_safety_canonical.py', 'Windows only; no new process owner code or three-platform acceptance'),
    'U01': ('Canonical HTTP Agent/User authorization remains independent of legacy scope', 'test_scheduler_surface_retirement.py', 'Legacy read scopes cannot disappear before G2/G3'),
    'U02': ('Old safe cancel remains for existing Runner ownership', 'test_job_retained_safety_canonical.py', 'Synthetic canonical cancel does not clear unknown real old owners'),
    'U03': ('Hidden child producer retired; old late callback must not recreate parent', 'test_job_retained_safety_canonical.py', 'No physical old cohort cleanup claim'),
    'U04': ('Scheduled child producer retired; terminal successor uses same canonical identity', 'test_job_retained_safety_canonical.py', 'Keep history and original receipt semantics'),
    'U05': ('Canonical Attempt fence remains; stale original fence must not acquire new identity', 'test_scheduler_pending_adoption.py', 'No real old lease inventory'),
    'U06': ('Typed system execution kept; old helpers cannot authorize new parallel owner', 'test_job_final_http_service.py', 'Lifecycle/recovery authority needs MA coordination'),
    'U07': ('Native schema refuses legacy fields; old validators mixed in facade', 'test_job_final_contracts.py', 'Whitespace-name semantic gap requires shared contract change'),
    'U08': ('schedule_calculations and data_paths extracted; cold same-grid recovery passes', 'test_scheduler_source_migration.py', 'No actual real source activation/cleanup proof'),
    'U09': ('P24 original task/job/run/dispatch/entry IDs readable and bytes unchanged', 'test_jobs_api.py; test_job_retained_history_bridge.py', 'History audit export/real index transfer before reader removal'),
    'U10': ('scheduler cron shim removed; jobs cron/calculator retained', 'test_scheduler_dependency_cutover.py', 'Pure calculator remains required; no broad file-name deletion'),
    'U11': ('Native Template passive composition supported', 'test_job_final_http_service.py', 'Full builtin/custom template provenance not reaccepted'),
    'U12': ('P24 independent config and metadata pin holds protect delete/GC', 'test_job_retained_canonical_pins.py; test_job_retained_canonical_pin_resolution.py', 'Old pins historical view still separate; UI/Settings parity missing'),
    'U13': ('server lifecycle helpers still query/create old service Job records', 'inventory.json surfaceCandidates', 'Original cross-stop operation settlement missing; keep until coordinated'),
    'U14': ('job_config_drafts remains guarded; canonical final_tools available', 'packages/mcp/server.py::job_config_drafts', 'Do not claim complete legacy configuration wrapper removal'),
    'U15': ('action_api and final_api coexist; native schema/operation owns new execution', 'test_job_final_http_service.py', 'Mixed read/history/authorization dependencies block entire router deletion'),
    'U16': ('MCP manifest explicitly describes retained reads/cancel and refuses old writes', 'test_scheduler_surface_retirement.py; packages/mcp/manifest.json', 'Manifest still intentionally advertises retained legacy controls; full retirement false'),
}
assert set(reviews) == {r['id'] for r in items}
for row in items:
    row['currentObservation'], row['currentEvidence'], row['remaining'] = reviews[row['id']]
strict = {
    'tests/test_jobs_api.py::test_resume_legal_running_scheduled_fire_without_candidates_is_not_backlogged': ['tests/test_job_retirement_remaining_successors.py::test_scheduled_fire_without_matching_candidates_is_noop_and_not_backlogged'],
    'tests/test_jobs_api.py::test_structured_identity_new_records': ['tests/test_job_final_http_service.py::test_ft01_real_crud_cas_idempotency_persisted_after_reload'],
    'tests/test_jobs_api.py::test_patch_enable_disable_stops_entries': ['tests/test_job_final_http_service.py::test_ft01_action_disable_after_admission_prevents_effect', 'tests/test_job_final_http_service.py::test_ft01_definition_retire_preserves_already_admitted_run'],
    'tests/test_jobs_api.py::test_action_template_default_assign': ['tests/test_job_final_contracts.py::test_fc04_legacy_fields_are_rejected_without_fallback[job-kind]'],
    'tests/test_jobs_api.py::test_action_template_send_session': ['tests/test_job_final_http_service.py::test_ft01_run_and_six_operations_use_same_wp02_identities'],
    'tests/test_jobs_api.py::test_action_template_unknown_falls_back_to_assign': ['tests/test_job_final_contracts.py::test_fc04_legacy_fields_are_rejected_without_fallback[job-kind]'],
    'tests/test_jobs_api.py::test_resume_legal_running_patch_preserves_dynamic_target_and_fixed_text': ['tests/test_job_retirement_closure_successors.py::test_target_and_text_edit_only_changes_future_frozen_runs'],
    'tests/test_jobs_api.py::test_resume_legal_running_keeps_per_fire_client_message_id_stable': ['tests/test_job_final_executors.py::test_r5_concurrent_same_effect_does_not_send_twice'],
    'tests/test_jobs_api.py::test_runs_and_next_and_run_now': ['tests/test_job_final_http_service.py::test_ft01_creator_trigger_template_and_retire_real_storage'],
    'tests/test_jobs_api.py::test_partial_failed_event_emitted': ['tests/test_job_final_executors.py::test_fe03_session_fanout_returns_per_target_receipts', 'tests/test_job_final_admission.py::test_fa03_notification_ack_uses_original_outbox_identity_and_never_resends_unknown[unknown-uncertain]'],
    'tests/test_jobs_api.py::test_post_creates_scheduled_task_multi_entry': ['tests/test_job_final_http_service.py::test_ft01_creator_trigger_template_and_retire_real_storage'],
    'tests/test_jobs_api.py::test_post_creates_scheduled_shell_action_without_target_or_runner': ['tests/test_job_final_executors.py::test_fe01_owned_process_preserves_argv_and_cleanup_proof', 'tests/test_job_final_executors.py::test_fe05_lifecycle_and_shell_are_explicitly_refused'],
    'tests/test_jobs_api.py::test_scheduled_qq_fire_uses_selected_bot_and_records_success_or_offline_error': ['tests/test_job_final_executors.py::test_fe04_qq_typed_error_and_timeout_are_distinct'],
    'tests/test_jobs_api.py::test_post_accepts_session_message_and_rejects_empty_schedule': ['tests/test_job_final_contracts.py::test_fc04_legacy_fields_are_rejected_without_fallback[job-schedule]'],
    'tests/test_jobs_api.py::test_patch_schedule_replaces_entries': ['tests/test_job_final_creators.py::test_fs04_shared_reference_cas_phase_recovery_and_template_composition'],
    'tests/test_jobs_api.py::test_patch_target_clear_enters_undeliverable': ['tests/test_job_final_contracts.py::test_fc03_trigger_target_and_job_links_must_stay_consistent'],
    'tests/test_jobs_api.py::test_run_records_carry_entry_id': ['tests/test_scheduler_source_migration.py::test_canonical_retirement_keeps_original_identity_links_requests_and_event_history'],
    'tests/test_jobs_api.py::test_patch_schedule_rejects_bad_spec': ['tests/test_job_retirement_remaining_successors.py::test_invalid_schedule_spec_is_rejected_before_creator_persistence'],
}
for row in nodes:
    if row['node'] in strict:
        row['status'] = 'strict-successor'
        row['strictSuccessors'] = strict[row['node']]
        row['currentExecution'] = 'revision3 strict matrix'
        row['boundary'] = 'Canonical successor preserves the decisive identity/state/effect assertion; provider-specific or real-cohort layers remain separate.'
# Preserve the dated, still-open review when rebuilding historical mapping.
# This overlay records evidence only; it cannot promote any original node.
review_path = OUT / 'gap-evidence-20261028.json'
if review_path.is_file():
    review = json.loads(review_path.read_text(encoding='utf-8'))
    assert review['closedGapCount'] == 0
    gaps = {row['node'] for row in nodes if row['status'] == 'gap'}
    assert gaps == set(review['nodes'])
    for row in nodes:
        if row['node'] in review['nodes']:
            item = review['nodes'][row['node']]
            assert item['status'] == 'gap' and item['closed'] is False
            row['currentGapReview'] = item
(OUT / 'successors.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'originalNodes': len(nodes), 'retirementItems': len(items)}))
