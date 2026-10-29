"""Supplement CP-X3's minimal runner with original compatibility tests."""
import argparse
import ast
import json
import platform
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SUPPLEMENTAL = (
    'tests/test_portability_paths.py', 'tests/test_terminal_secret_store.py',
    'tests/test_plugin_event_lifecycle.py', 'tests/test_plugin_events.py',
    'tests/test_minimal_ci_helper.py',
)
LEGACY = (
    'tests/test_scheduler_api.py::test_create_list_get_roundtrip',
    'tests/test_scheduler_api.py::test_update_task_changes_fields',
    'tests/test_scheduler_api.py::test_delete_task_removes_it',
    'tests/test_scheduler_store.py::test_create_writes_job_record',
    'tests/test_scheduler_store.py::test_update_recomputes_next_fire_on_schedule_change',
    'tests/test_background_jobs.py::test_start_persists_metadata_and_detaches',
    'tests/test_background_jobs.py::test_accepted_cancel_wins_runner_exit_race',
    'tests/test_background_jobs.py::test_retry_inherits_creator_and_target',
    'tests/test_background_job_api.py::test_mcp_get_list_cancel_retry_use_managed_target',
    'tests/test_jobs_api.py::test_post_creates_scheduled_task_multi_entry',
    'tests/test_jobs_api.py::test_patch_rename_and_description',
    'tests/test_jobs_api.py::test_delete_job_routes_to_compat_for_scheduled_task',
)
BROWSER = ('tests/test_user_locker_browser.py',)
REQUIRED = {
    'tests/test_worker_platform_admission.py': ('test_stream_spawn_preserves_literal_cwd_and_argv', 'test_native_start_rejected_before_recovery_or_spawn'),
    'tests/test_native_platform_projection.py': ('test_creation_rejects_native_before_session_write',),
    'tests/test_native_control_api.py': ('test_explicit_platform_denial_precedes_configuration_side_effects',),
    'tests/test_plugin_ipc_endpoint.py': ('test_long_caller_path_concurrent_bridges_roundtrip_and_cleanup', 'test_long_temp_root_uses_short_private_endpoint'),
    'tests/test_minimal_platform_release.py': ('test_preexisting_profile_keeps_original_jobs_across_owned_start', 'test_ordinary_pipe_worker_owned_io_stop'),
}


def missing_contracts(root):
    missing = []
    for filename, names in REQUIRED.items():
        path = root / filename
        if not path.is_file():
            missing.append(filename)
            continue
        definitions = {node.name for node in ast.parse(path.read_text(encoding='utf-8')).body
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        missing.extend(filename + '::' + name for name in names if name not in definitions)
    return missing


def command(phase, out):
    nodes = {'supplemental': SUPPLEMENTAL, 'legacy': LEGACY, 'browser': BROWSER}[phase]
    child = [sys.executable, '-m', 'pytest', *nodes, '-q', '-ra',
             f'--junitxml={out / (phase + ".xml")}']
    if phase == 'browser':
        child.append(f'--basetemp={out / "owned-tmp"}')
    return [sys.executable, str(ROOT / 'scripts/run_tests_isolated.py'), '--', *child]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', required=True, choices=('supplemental', 'legacy', 'browser', 'check'))
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    argv = command(args.phase, out) if args.phase != 'check' else []
    started = time.monotonic()
    with (out / (args.phase + '.log')).open('w', encoding='utf-8') as log:
        missing = missing_contracts(ROOT) if args.phase == 'check' else []
        if missing:
            log.write('Required extracted contracts missing:\n' + '\n'.join(missing) + '\n')
            exit_code = 4
        elif args.phase == 'check':
            log.write('Required extracted contract definitions present.\n')
            exit_code = 0
        else:
            exit_code = subprocess.run(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    (out / (args.phase + '-result.json')).write_text(json.dumps({
        'schema': 'pan.minimal-ci.phase/1', 'sourceSha': source, 'phase': args.phase,
        'argv': argv, 'exitCode': exit_code, 'seconds': round(time.monotonic()-started, 3),
        'python': platform.python_version(), 'host': platform.platform(),
    }, indent=2) + '\n', encoding='utf-8')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
