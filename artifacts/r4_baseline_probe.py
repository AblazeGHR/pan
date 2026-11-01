"""Read baseline modules into an isolated pytest child without editing the tree.

Run through scripts/run_tests_isolated.py. The three selected legacy tests are
unchanged from the baseline; source substitution covers tracked product edits.
"""
import importlib.abc
import importlib.util
import subprocess
import sys
from pathlib import Path

repo = Path.cwd()
changed = subprocess.check_output(['git','diff','43d0977f','--name-only','--','*.py'],text=True).splitlines()
sources = {}
for relative in changed:
    result = subprocess.run(['git','show','43d0977f:'+relative],capture_output=True)
    if result.returncode:
        continue
    name = relative.removesuffix('.py').replace('/','.')
    package = name.endswith('.__init__')
    if package: name = name.removesuffix('.__init__')
    sources[name] = (repo / relative, result.stdout, package)
class BaselineLoader(importlib.abc.Loader):
    def __init__(self, item): self.item = item
    def create_module(self, spec): return None
    def exec_module(self, module):
        path, source, _ = self.item
        module.__file__ = str(path)
        exec(compile(source,str(path),'exec'),module.__dict__)
class BaselineFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in sources: return None
        item = sources[fullname]
        return importlib.util.spec_from_file_location(fullname,str(item[0]),loader=BaselineLoader(item),
            submodule_search_locations=[str(item[0].parent)] if item[2] else None)
sys.meta_path.insert(0,BaselineFinder())
import pytest
raise SystemExit(pytest.main(['tests/test_automation_concurrency.py::test_empty_snapshot_then_match_cannot_lose_wakeup',
    'tests/test_automation_concurrency.py::test_disable_between_claim_and_dependency_deferral_cancels',
    'tests/test_automation_retry_parallel.py::test_uncertain_confirmation_keeps_run_and_new_attempt',
    '-q','-o','addopts=','--tb=short','--junitxml=artifacts/r4-baseline-legacy-red.xml']))
