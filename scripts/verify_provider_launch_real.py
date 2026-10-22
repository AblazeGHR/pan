"""Frozen-source verification of installed Codex + Pan wrapper, loopback only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/provider-launch-real'

def main():
    env=dict(os.environ)
    for key in ('PYTHONUTF8','PYTHONIOENCODING'):env.pop(key,None)
    if not Path(env.get('TEST_REAL_CODEX_JS','')).is_file():
        raise RuntimeError('Set TEST_REAL_CODEX_JS to the installed codex.js explicitly')
    def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT).decode('utf-8').strip()
    if any(not p.startswith('artifacts/') for p in git('diff','--name-only').splitlines()):
        raise RuntimeError('Commit/freeze source before final verification')
    manifest={'sourceSha':git('rev-parse','HEAD'),'branch':git('branch','--show-current'),
              'python':sys.executable,'cwd':str(ROOT),'checks':[],
              'realAccountCalls':0,'paidModelCalls':0,'existingServiceOperations':0}
    manifest['encoding']=json.loads(subprocess.check_output([sys.executable,'-c',
        'import json,sys,locale,os;print(json.dumps(dict(utf8Mode=sys.flags.utf8_mode,localeEncoding=locale.getencoding(),PYTHONUTF8=os.environ.get("PYTHONUTF8"),PYTHONIOENCODING=os.environ.get("PYTHONIOENCODING"))))'],env=env).decode('utf-8'))
    OUT.mkdir(parents=True,exist_ok=True)
    groups={
        'real-installed':['tests/test_provider_launch_real_codex.py'],
        'launch-regressions':['tests/test_provider_launch_native_compat.py','tests/test_provider_launch.py'],
        'wrapper-regressions':['tests/test_codex_large_resume.py','tests/test_codex_adapter.py'],
    }
    checks=[('diff-check',['git','diff','--check','19c294a5','HEAD'],None)]
    checks += [(name,[sys.executable,'scripts/run_tests_isolated.py','--',sys.executable,'-m','pytest',*tests,'-q',
                      '--junitxml=artifacts/provider-launch-real/'+name+'.xml'],OUT/(name+'.xml')) for name,tests in groups.items()]
    checks += [('compile',[sys.executable,'-m','compileall','-q','packages/core/adapters/codex/provider_launch.py',
                    'packages/core/adapters/codex/app_server_wrapper.py','tests/test_provider_launch_native_compat.py',
                    'tests/test_provider_launch_real_codex.py','scripts/verify_provider_launch_real.py'],None)]
    with tempfile.TemporaryDirectory(prefix='pan-native-verify-') as home:
        env['CODEX_HOME']=home
        for name,argv,junit in checks:
            started=time.monotonic()
            result=subprocess.run(argv,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=100)
            try:log=result.stdout.decode('utf-8')
            except UnicodeDecodeError:
                (OUT/(name+'.raw')).write_bytes(result.stdout)
                log=result.stdout.decode('utf-8',errors='replace')
            (OUT/(name+'.log')).write_text(log.replace('\r\n','\n'),encoding='utf-8',newline='\n')
            row={'name':name,'argv':argv,'exitCode':result.returncode,'seconds':round(time.monotonic()-started,3)}
            if junit:
                row['counts']={k:sum(int(s.get(k,0)) for s in ET.parse(junit).getroot().iter('testsuite')) for k in ('tests','failures','errors','skipped')}
            manifest['checks'].append(row)
            (OUT/'verification.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
            print(name+': exit '+str(result.returncode),flush=True)
            if result.returncode:return result.returncode
    return 0

if __name__=='__main__':raise SystemExit(main())
