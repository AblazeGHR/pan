import pathlib
import sys
import time
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

class StallBeforeWatchdog:
    def pytest_collection_modifyitems(self, items):
        module = items[0].module
        original = module._run_watchdog
        async def stalled(worker, ticks=5):
            time.sleep(.15)
            return await original(worker, ticks)
        module._run_watchdog = stalled

raise SystemExit(pytest.main([
    "tests/test_worker_watchdog.py::test_active_running_worker_not_killed",
    "-q", "-ra", "--junitxml=" + sys.argv[1],
], plugins=[StallBeforeWatchdog()]))
