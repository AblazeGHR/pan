"""Replay only the authorized baseline ProcessOwner module, in this test child."""
import subprocess
import sys
import types
import pytest

class Baseline:
    def pytest_sessionstart(self, session):
        import packages.core.platform.process as current
        name = 'packages.core.platform._cpx2_original_process'
        module = types.ModuleType(name)
        module.__package__ = 'packages.core.platform'
        sys.modules[name] = module
        source = subprocess.check_output(['git', 'show', 'a9d236c1c16159ca27697cfe090e3b7256c8c417:packages/core/platform/process.py'])
        exec(compile(source, '<exact-a9d236c1-process.py>', 'exec'), module.__dict__)
        current.WindowsProcessOwner = module.WindowsProcessOwner

default_nodes = [
    'tests/test_platform_process_windows_hidden.py::test_hidden_tree_identity_stdio_and_desktop_cleanup[base]',
    'tests/test_platform_process_windows_hidden.py::test_atomic_membership_precedes_resume_and_no_late_assign',
    '-q', '-o', 'addopts=', '--tb=short',
    '--junitxml=evidence/cp-x2-windows-hidden-20261028/baseline-red.xml']
raise SystemExit(pytest.main(sys.argv[1:] or default_nodes, plugins=[Baseline()]))
