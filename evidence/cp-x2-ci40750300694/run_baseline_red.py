"""Exact fc5604ab named-runtime test with a directly installed interpreter."""
from pathlib import Path
import subprocess
import sys
import tempfile
import types
source = subprocess.check_output(['git', 'show', 'fc5604ab8fc471f04fe91a868ca6938a11e48123:tests/test_platform_process_windows_hidden.py'])
module = types.ModuleType('cpx2_exact_old_hidden')
module.__file__ = str(Path('tests/test_platform_process_windows_hidden.py').resolve())
exec(compile(source, '<fc5604ab-hidden-test>', 'exec'), module.__dict__)
sys.executable = sys._base_executable
with tempfile.TemporaryDirectory(prefix='cpx2-direct-baseline-') as root:
    module.test_hidden_tree_identity_stdio_and_desktop_cleanup(Path(root), 'named-venv')
