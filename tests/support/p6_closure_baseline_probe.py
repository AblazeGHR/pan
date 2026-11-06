"""Replay remaining combination failures against pinned ed55 product source.

No checkout/worktree mutation. Used only with unchanged historical assertions;
new handoff modules/tests are not loaded by this probe's selected cases.
"""
import importlib.abc
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BASELINE = 'ed55d259fc3214b361a8296a9da60436d96a001f'
MODULES = {'packages.automation.api', 'packages.jobs.action_api',
           'packages.jobs.final_api', 'packages.jobs.final_service',
           'packages.jobs.runtime_owner', 'packages.scheduler.source_migration'}


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
