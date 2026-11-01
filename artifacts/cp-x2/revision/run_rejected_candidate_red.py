import subprocess,sys,types
import packages.core.platform as platform
name='packages.core.platform.process_posix'
source=subprocess.run(['git','show','43765dbbec4baee7c6d24007b16255a76f496b93:packages/core/platform/process_posix.py'],check=True,capture_output=True).stdout.decode('utf-8')
module=types.ModuleType(name); module.__file__=name
sys.modules[name]=module; platform.process_posix=module
exec(compile(source,name,'exec'),module.__dict__)
import pytest
raise SystemExit(pytest.main(['tests/test_platform_process_posix_unit.py','-q','-o','addopts=','--tb=line','-k','readonly_empty_tree or dead_recovery_can or recovery_uses_exact','--junitxml=artifacts/cp-x2/revision/rejected-candidate-red.xml']))
