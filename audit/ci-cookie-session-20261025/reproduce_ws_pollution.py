"""Reproduce the unchanged lifecycle assertions with inherited fake WS state.

Run through scripts/run_tests_isolated.py. No listener or supervisor is started.
The normal pytest WebSocket isolation fixture is deliberately not invoked here.
"""
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from packages.core import background_jobs, config, session
import packages.web.server as server
from tests import test_main_service_exit as exit_tests
from tests import test_main_service_restart as restart_tests


class FakeDashboard:
    async def send_json(self, data):
        pass


def main():
    results = []
    for module, test in [
        (exit_tests, exit_tests.test_exit_job_records_choice_and_exact_pre_exit_running_snapshot),
        (restart_tests, restart_tests.test_restart_returns_scheduled_before_python_supervisor_finishes),
    ]:
        with TemporaryDirectory(prefix='pan-cookie-pollution-') as temporary, pytest.MonkeyPatch.context() as patch:
            root = Path(temporary)
            patch.setattr(config, 'CONFIG_FILE', root / 'config.json')
            patch.setattr(session, 'SESSION_DIR', root / 'sessions')
            patch.setattr(background_jobs, '_scheduled_task_hooks', {
                **background_jobs._scheduled_task_hooks, 'on_event': server.broadcast})
            patch.setattr(server, 'ws_clients', {FakeDashboard()})
            patch.setattr(server, '_ws_outbound', {})
            module.isolated.__wrapped__(root, patch)
            try:
                test(patch)
            except AssertionError as error:
                captured = []
                traceback = error.__traceback__
                while traceback:
                    local = traceback.tb_frame.f_locals
                    captured.extend(local.get('created_tasks', []))
                    for coroutine in local.get('tasks', []):
                        if inspect.iscoroutine(coroutine):
                            captured.append(coroutine.cr_code.co_name)
                            coroutine.close()
                    traceback = traceback.tb_next
                results.append({'test': test.__name__, 'inheritedDashboardCount': 1,
                                'originalAssertionFailed': True, 'capturedTasks': captured,
                                'senderChannels': [client.kind for client in server._ws_outbound.values()]})
            else:
                raise AssertionError('Expected the original lifecycle assertion to fail')
    output = Path('artifacts/ci-cookie-lifecycle-pollution.json')
    output.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
