"""Proposed MA CI entry for the independent main9b4506 platform candidate.

Does not install dependencies, build assets, dispatch CI, or change product data.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FAST = [
    'tests/test_platform_layer.py', 'tests/test_platform_api.py',
    'tests/test_platform_import_chain.py', 'tests/test_worker_platform_admission.py',
    'tests/test_native_platform_projection.py', 'tests/test_native_control_api.py',
    'tests/test_plugin_ipc_endpoint.py', 'tests/test_minimal_platform_release.py',
    'tests/test_background_job_api.py', 'tests/test_scheduler_store.py', 'tests/test_scheduler_api.py',
    'tests/test_background_jobs.py::test_start_persists_metadata_and_detaches',
    'tests/test_background_jobs.py::test_external_cwd_runs_and_projects_one_completion_notice',
    'tests/test_plugin_event_lifecycle.py', 'tests/test_plugin_events.py',
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=['import', 'collect', 'fast', 'browser'], required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.phase == 'import':
        child = [sys.executable, '-c', 'import packages.web.server as s; assert s.app is not None; print("server import OK")']
    else:
        nodes = (['tests/', 'packages/qq/', 'packages/wechat/'] if args.phase == 'collect' else
                 ['tests/test_minimal_platform_browser.py'] if args.phase == 'browser' else FAST)
        child = [sys.executable, '-m', 'pytest', *nodes, '-q', '-ra', f'--junitxml={out / (args.phase + ".xml")}']
        if args.phase == 'collect':
            child.append('--collect-only')
    command = [sys.executable, str(ROOT / 'scripts/run_tests_isolated.py'), '--', *child]
    with (out / (args.phase + '.log')).open('w', encoding='utf-8') as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    (out / (args.phase + '-result.json')).write_text(json.dumps({
        'source': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'argv': command, 'exitCode': result.returncode, 'platform': sys.platform,
    }, indent=2))
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
