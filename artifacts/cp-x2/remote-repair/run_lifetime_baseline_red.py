import subprocess,sys,types
import packages.core.platform as platform
name='packages.core.platform.process_darwin'
source=subprocess.run(['git','show','036fe85f23b689195207d6794db0a70d78d87fc2:packages/core/platform/process_darwin.py'],check=True,capture_output=True).stdout.decode('utf-8')
module=types.ModuleType(name); module.__file__=name
sys.modules[name]=module; platform.process_darwin=module
exec(compile(source,name,'exec'),module.__dict__)
import pytest
raise SystemExit(pytest.main(['tests/test_platform_process_posix_unit.py','-q','-o','addopts=','--tb=short','-k','same_lifetime_across_exec or native_abi_layout_and_single_generation','--junitxml=artifacts/cp-x2/remote-repair/lifetime-baseline-red.xml']))
