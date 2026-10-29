"""Map exact historical pytest records to source assertions and bounded proof."""
import ast
import collections
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
REMOTE = Path('D:/project/Pan/.workflow/job-unification-review-20261028/remote-ci-40760691765/artifacts-recovered')
BASELINE = '542be56e3b2097bd3a3d61ee153202c696b65a60'
CURRENT = 'c9137b0a5f075c44b03fa5e41777949813d4b663'
MODULES = {'test_jobs_api', 'test_background_job_api', 'test_job_hidden_production',
    'test_job_window_policy', 'test_job_runner_venv', 'test_background_job_retry_if',
    'test_background_jobs', 'test_independent_job_bridge', 'test_main_service_exit',
    'test_main_service_restart', 'test_legal_offline_http', 'test_job_lifecycle_prevention'}


def load_results(path):
    results = collections.defaultdict(list)
    for case in ET.parse(path).iter('testcase'):
        node = case.get('classname', '').replace('tests.', 'tests/', 1) + '.py::' + case.get('name', '')
        status = next((x.tag for x in case if x.tag in {'failure', 'error', 'skipped'}), 'passed')
        results[node.split('[')[0]].append({'node': node, 'status': status})
    return results


current = collections.defaultdict(list)
for f in ['current-representatives.xml', 'current-canonical.xml', 'current-job-contracts.xml', 'current-boundaries.xml']:
    for node, entries in load_results(OUT / f).items():
        current[node].extend({**entry, 'sourceSha': CURRENT, 'artifact': 'evidence/cp-x1-full-red-closure-20261029/' + f} for entry in entries)

historical = json.loads((ROOT / 'evidence/job-retirement-closure/successors.json').read_text(encoding='utf-8'))
mapping = {x['node']: x for x in historical['originalNodes']}
lifecycle = json.loads((ROOT / 'docs/reports/CP_X1_LIFECYCLE_GATE_MAPPING.json').read_text(encoding='utf-8'))
life_map = {x['originalNode']: x for x in lifecycle['successors']}
sources = {}
for mod in MODULES:
    rel = f'tests/{mod}.py'
    content = subprocess.check_output(['git', 'show', f'{BASELINE}:{rel}'], cwd=ROOT).decode('utf-8')
    tree = ast.parse(content)
    functions = {}
    for fn in tree.body:
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name.startswith('test_'):
            assertions = [ast.get_source_segment(content, x) for x in ast.walk(fn) if isinstance(x, ast.Assert)]
            raises = [ast.get_source_segment(content, x.context_expr) for x in ast.walk(fn)
                      if isinstance(x, (ast.With, ast.AsyncWith))
                      for x in x.items if 'pytest.raises' in (ast.get_source_segment(content, x.context_expr) or '')]
            body = ast.get_source_segment(content, fn)
            functions[fn.name] = {'line': fn.lineno, 'docstring': ast.get_docstring(fn),
                'original_assertions': assertions, 'expected_exceptions': raises,
                'function_sha256': hashlib.sha256(body.encode()).hexdigest()}
    sources[mod] = {'path': rel, 'sourceSha': BASELINE, 'source_sha256': hashlib.sha256(content.encode()).hexdigest(), 'functions': functions}

REMOTE_PROOF = {'sourceSha': BASELINE, 'run': 40760691765,
    'artifacts': ['windows-owned-combination-3.12/owned-combination.xml', 'windows-owned-combination-3.14/owned-combination.xml'],
    'nodes': ['tests/test_lifecycle_controller_publication.py::test_canonical_job_cancel_after_controller_crash',
              'tests/test_platform_process_windows_hidden.py::test_hidden_tree_identity_stdio_and_desktop_cleanup',
              'tests/test_platform_process_windows_hidden.py::test_visible_intent_does_not_allocate_hidden_desktop',
              'tests/test_platform_process_windows_hidden.py::test_runner_hard_death_releases_whole_tree_and_desktop'],
    'scope': '25 passed each version; only partial process ownership, desktop and public cancel coverage, not identical legacy GUI/IPC/automatic policy'}


def group(mod, name):
    if mod == 'test_jobs_api':
        return ('canonical_jobs_public_api', 'packages.jobs.final_service.FinalService and /api/jobs canonical definitions/Run/Creator',
                'Use the existing canonical HTTP/CAS/Run entry for the original assertion; retain exact target, retry, auth and event behavior; where only an old envelope failed, prove the named successor rather than restoring old ok fields.')
    if mod == 'test_background_job_retry_if':
        return ('legacy_automatic_retry', 'packages.jobs.final_service.FinalService.control_run / ExecutionStore Attempt fence',
                'Compare this exact reason/budget/backoff/cancel parameter to a canonical policy on the same Run/effect; preserve original Attempt history and cleanup proof. Existing explicit retry is partial; automatic=true remains capability_unsupported and requires MA policy decision, never replay unknown.')
    if mod == 'test_background_jobs':
        if any(x in name for x in ('broadcast', 'notification', 'notice')):
            return ('terminal_notification_identity', 'FinalService terminal receipt/outbox and original Session delivery acknowledgement',
                    'Prove the original once-only notice, channel failure and restart behavior with the same event/effect identity; query unknown before retry and do not restore legacy registry notifications.')
        if any(x in name for x in ('cancel', 'claim')):
            return ('process_cancellation_ownership', 'ExecutionStore full Attempt/lease/generation fence, public Run cancel and ProcessOwner',
                    'Drive the original cancellation/claim race through public canonical admission and original owner proof; retain birth identity, cleanup outcome and unknown without re-launch.')
        return ('process_job_contract', 'FinalService native process Action and independent ExecutionStore',
                'Express this original assertion through the owned canonical process Action, receipt and public Job/Run identity; retain invalid-input/no-spawn, cwd/log/creator or exact retry requirement from its assertion source.')
    if mod == 'test_background_job_api':
        return ('legacy_background_http_facade', 'canonical /api/jobs, /api/job-runs and typed process/session providers',
                'Replace only the obsolete facade/test double with a real canonical request and preserve the original validation, creator/target provenance, scoped access and no-side-effect assertions for this node.')
    if mod in {'test_job_hidden_production', 'test_job_window_policy', 'test_job_runner_venv'}:
        return ('windows_process_visibility', 'packages.core.platform.process / ProcessOwner and public canonical process Run',
                'Use the actual owned canonical runner and retained birth/Job/desktop proof; extend only the missing original argv, shell, stdout/stderr, file IPC, early window/GUI or visible-policy assertion for this node. The existing 25-node ownership gate is partial, not blanket GUI or legacy runner equivalence.')
    if mod == 'test_independent_job_bridge':
        return ('retired_independent_job_facade', 'FinalService public Job/Run and independent ExecutionStore projection',
                'Use a real canonical client and scoped read/control boundary; assert the original public identity, remote ownership and refusal to mutate local historical metadata without reintroducing retired bridge writes.')
    if mod == 'test_job_lifecycle_prevention':
        return ('controller_crash_and_owned_tree', 'public canonical Run cancellation / ExecutionStore and ProcessOwner',
                'Map this original crash point onto claim-before-spawn or after-running; retain original Attempt/effect, PID birth, desktop/tree cleanup and no-replay assertions. Do not manufacture the removed child-before-claim window.')
    if mod == 'test_legal_offline_http':
        return ('owned_host_lifecycle', 'server.api_system_main_lifecycle / ProtectedLifecycleExecutor / PanServer',
                'Repeat only this exact API or console path with a genuine owned main identity and original offline/preserve/singleflight assertions; prior a9 proof is historical and signal fixture 404s are not product evidence.')
    return ('lifecycle_transition', 'server.api_system_main_lifecycle / _canonical_lifecycle_handoff / ProtectedLifecycleExecutor',
            'Replace only the obsolete registry/task-name observation with the original canonical Attempt/effect and preserve this node’s ask, snapshot, gate, shutdown ordering and busy assertions.')


SPECIAL = {
 'test_patch_blank_name_rejected': ['tests/test_job_retirement_closure_successors.py::test_blank_name_rejection_preserves_definition_operation_and_disk'],
 'test_post_scheduled_qq_action_persists_target_and_rejects_untrusted_fields': ['tests/test_job_retirement_provider_boundaries.py::test_qq_untrusted_literal_must_be_rejected_before_committing'],
 'test_patch_scheduled_qq_action_is_strict_and_editable': ['tests/test_job_retirement_provider_boundaries.py::test_qq_untrusted_literal_must_be_rejected_before_committing'],
 'test_post_requires_existing_session': ['tests/test_job_retirement_provider_boundaries.py::test_missing_session_must_be_rejected_before_job_definition_commit'],
}
RETRY_PROOFS = ['tests/test_job_final_independent_execution.py::test_fe02_retry_keeps_job_run_step_effect_with_new_attempt_history',
 'tests/test_job_final_independent_execution.py::test_fe02_retry_needs_cleanup_proof',
 'tests/test_job_final_independent_execution.py::test_fe02_success_unknown_partial_cannot_be_replayed',
 'tests/test_job_final_independent_execution.py::test_fe02_stale_fence_never_settles_or_cancels',
 'tests/test_job_final_http_service.py::test_ft02_unbound_automatic_recovery_is_explicit_unsupported']
LIFE_PROOFS = ['tests/test_lifecycle_fast_contract.py::test_fast_ask_requires_choice',
 'tests/test_lifecycle_fast_contract.py::test_fast_nonblocking_singleflight']

output = {'schema': 'cp-x1.full-red-closure/1', 'historicalSourceSha': BASELINE,
 'currentSourceSha': CURRENT, 'run': 40760691765, 'sources': sources,
 'historicalMapping': {'path': 'evidence/job-retirement-closure/successors.json',
   'mappingBaseline': historical['baseline'], 'warning': 'historical labels are not exact-current proof; individual named test outcomes are separate'},
 'versions': {}}
for version in ('3.12', '3.14'):
    file = REMOTE / f'pytest-results-{version}' / 'pytest-results.xml'
    records = []
    for case in ET.parse(file).iter('testcase'):
        mod = case.get('classname', '').split('.')[-1]
        if mod not in MODULES:
            continue
        name = case.get('name', '')
        fn = name.split('[')[0]
        node = f'tests/{mod}.py::{name}'
        base = f'tests/{mod}.py::{fn}'
        for failure in case:
            if failure.tag not in {'failure', 'error'}:
                continue
            trace = failure.text or ''
            h = mapping.get(node) or mapping.get(base)
            l = life_map.get(node) or life_map.get(base)
            category, seam, next_step = group(mod, fn)
            proof_nodes = list(SPECIAL.get(fn, []))
            if h:
                proof_nodes += h.get('strictSuccessors', [])
            if l:
                proof_nodes += l.get('fastSuccessorNodes', [])
            if mod == 'test_background_job_retry_if':
                proof_nodes += RETRY_PROOFS
            if mod in {'test_main_service_exit', 'test_main_service_restart'}:
                proof_nodes += LIFE_PROOFS
            if mod == 'test_background_jobs' and any(x in fn for x in ('retry', 'cancel', 'claim')):
                proof_nodes += RETRY_PROOFS[:4]
            if mod == 'test_background_jobs' and any(x in fn for x in ('broadcast', 'notification', 'notice')):
                proof_nodes += ['tests/test_job_final_independent_execution.py::test_r5_ack_crash_rolls_back_and_redelivers_same_original_receipt']
            if mod == 'test_independent_job_bridge':
                proof_nodes += ['tests/test_job_final_independent_execution.py::test_fe02_canonical_client_http_receipt_and_ack_do_not_use_legacy_jobs']
            matched = [entry for key in dict.fromkeys(proof_nodes) for entry in current.get(key.split('[')[0], [])]
            item = {'node': node, 'outcome': failure.tag,
                'trace_sha256': hashlib.sha256(trace.encode()).hexdigest(),
                'original_message': failure.get('message', '').splitlines()[0],
                'failure_observation': 'explicit_retired_entrypoint' if 'RetiredEntryPoint' in trace or 'RetiredEntryPoint' in failure.get('message', '') else 'original_failure_or_setup_error',
                'behavior_source': f'{mod}::{fn}', 'group': category, 'current_seam': seam,
                'historical_successor_mapping': ({k: h.get(k) for k in ('status','strictSuccessors','boundary','followup')} if h else None),
                'lifecycle_mapping': l, 'current_exact_proof': matched,
                'required_remaining_action': next_step,
                'classification': 'partial_or_unverified_no_automatic_waiver'}
            if item['failure_observation'] == 'explicit_retired_entrypoint':
                item['classification'] = 'retired_entrypoint_observed_original_behavior_still_requires_successor'
            if fn in SPECIAL and matched:
                item['classification'] = 'exact_current_canonical_subcontract_pass_original_node_still_red'
            if mod in {'test_job_hidden_production','test_job_window_policy','test_job_runner_venv','test_job_lifecycle_prevention'}:
                item['related_owned_process_proof'] = REMOTE_PROOF
            if mod == 'test_legal_offline_http':
                item['earlier_owned_host_proof'] = {'sourceSha': 'a9d236c1c16159ca27697cfe090e3b7256c8c417',
                    'artifact': 'D:/project/Pan/.workflow/job-unification-review-20261028/lifecycle-v2-final-ma-owned.xml',
                    'scope': 'seven safe/preserve/q cases passed on earlier source; four genuine-main signal cases were fixture 404'}
            if fn == 'test_resume_legal_running_scheduled_fire_without_candidates_is_not_backlogged':
                item['semantic_difference'] = 'Historical named successor only proves unmatched event type, not a matching event with an empty legal-offline Session query.'
                item['classification'] = 'matching_empty_resume_selection_not_proven'
            if fn == 'test_resume_legal_running_patch_preserves_dynamic_target_and_fixed_text':
                item['semantic_difference'] = 'Current named fixed-target frozen-run proof does not establish dynamic legal-offline selection and fixed resume text.'
                item['classification'] = 'dynamic_resume_target_not_proven'
            if fn in {'test_action_template_unknown_falls_back_to_assign', 'test_action_template_default_assign'}:
                item['semantic_difference'] = 'The historical named no-legacy-fallback rejection test does not establish the original default/unknown-to-assign behavior; check intended canonical Template composition and user-approved retirement boundary.'
                item['classification'] = 'historical_successor_does_not_prove_original_fallback'
            if fn == 'test_post_creates_scheduled_shell_action_without_target_or_runner':
                item['semantic_difference'] = 'The named owned-argv process test plus a test that explicitly refuses shell does not by itself establish the original scheduled shell execution capability.'
                item['classification'] = 'scheduled_shell_capability_not_proven_by_named_successors'
            if fn == 'test_scheduled_qq_fire_uses_selected_bot_and_records_success_or_offline_error':
                item['semantic_difference'] = 'A typed test provider distinguishing error and timeout does not prove selected-bot binding or real QQ transport consumption.'
                item['classification'] = 'real_qq_provider_and_selected_bot_unverified'
            if fn in {'test_resume_legal_running_job_rescans_and_run_now_sends_only_to_current_candidates', 'test_resume_legal_running_empty_scan_is_successful_noop'}:
                item['semantic_difference'] = 'Current dynamic session_operation resolves selection but invokes pause only; resume and matching-zero completion require a native authorized successor.'
                item['classification'] = 'required_canonical_resume_boundary_not_proven_source_gap'
            if fn in {'test_post_scheduled_qq_action_persists_target_and_rejects_untrusted_fields', 'test_patch_scheduled_qq_action_is_strict_and_editable'}:
                item['semantic_difference'] = 'Current exact-source tests prove invalid numeric target create/update rejection for a test-published typed channel; full original field matrix and actual bound QQ transport remain separate.'
            if fn in {'test_scheduled_qq_run_now_is_targetless_and_records_plugin_failure', 'test_scheduled_qq_backlog_retry_runs_without_session_target'}:
                item['semantic_difference'] = 'provider_unavailable and explicit retry safety are not proof of a bound real QQ plugin or same-effect retry after provider reconciliation.'
                item['current_exact_proof'] += current['tests/test_job_retirement_provider_boundaries.py::test_channel_without_bound_plugin_records_failure_and_never_invents_time_event']
                item['classification'] = 'real_qq_provider_and_same_effect_retry_unverified'
            records.append(item)
    assert len(records) == 188, (version, len(records))
    output['versions'][version] = {'raw_xml': str(file).replace('\\', '/'), 'xml_sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
        'records': records, 'counts': dict(collections.Counter(x['group'] for x in records))}

(OUT / 'per-node-disposition.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print({v: {'records': len(x['records']), 'groups': x['counts']} for v, x in output['versions'].items()})
