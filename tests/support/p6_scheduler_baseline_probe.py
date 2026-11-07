"""Read-only baseline replay for unchanged retirement assertions.

Run through run_tests_isolated.py. Load each product module changed by this
slice from the pinned parent via git show; other modules/assertions stay on
the same unchanged parent tree. No checkout, source edit or service is used.
"""
import importlib.abc
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BASELINE = '911935d95dc212af79398f1af684717f6a65e1e4'
MODULES = {'packages.automation.authorization', 'packages.jobs.final_service',
           'packages.mcp.server', 'packages.scheduler.api'}


class Baseline(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in MODULES:
            return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        relative = module.__name__.replace('.', '/') + '.py'
        source = subprocess.check_output(['git', 'show', BASELINE + ':' + relative], cwd=ROOT)
        module.__file__ = str(ROOT / relative)
        exec(compile(source, module.__file__, 'exec'), module.__dict__)


if __name__ == '__main__':
    sys.meta_path.insert(0, Baseline())
    import pytest
    raise SystemExit(pytest.main(sys.argv[1:]))
