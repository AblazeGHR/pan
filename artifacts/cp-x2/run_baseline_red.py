import subprocess, sys, types
import packages.core.platform as platform
name = 'packages.core.platform.process_posix'
source = subprocess.run(['git', 'show', '20955b3c8959c881797d84bcd03302158ddf08c3:packages/core/platform/process_posix.py'], check=True, capture_output=True).stdout.decode('utf-8')
module = types.ModuleType(name)
module.__file__ = name
sys.modules[name] = module
platform.process_posix = module
exec(compile(source, name, 'exec'), module.__dict__)
import pytest
raise SystemExit(pytest.main(['tests/test_platform_process_posix_unit.py', '-q', '-o', 'addopts=', '--tb=line', '-k', 'weak_birth or stale_group or no_escalation or macos_spawn', '--junitxml=artifacts/cp-x2/baseline-behavior-red.xml']))
