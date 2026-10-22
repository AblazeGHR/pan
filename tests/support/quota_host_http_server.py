"""Own profile/CLI only. Production host IO factory replaced before server import."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from packages.automation import quota_host
from packages.core.codex_quota_store import CodexProfile,CodexQuotaStore
from packages.jobs.window import command_window_options


def owned(store,worker,root):
    data=Path(os.environ['PAN_E2E_DATA_ROOT']).resolve()
    assert Path(root).resolve()==data
    profile=CodexProfile(data/'fixture-codex','native-fixture','native-fixture','fixture-profile')
    def runner(args):
        env=dict(os.environ);env['PAN_TEST_QUOTA_HOST_TRACE']=str(data/'host-cli.jsonl')
        value=subprocess.run([sys.executable,str(ROOT/'tests/fixtures/quota_host_cli.py'),*args],
            env=env,capture_output=True,encoding='utf-8',errors='strict',timeout=5,stdin=subprocess.DEVNULL,
            **command_window_options())
        if value.returncode:raise RuntimeError('fixture CLI failure')
        return json.loads(value.stdout)
    source=quota_host.ReadSource(data,bridge=type('FixtureBridge',(),{'runner':staticmethod(runner)})(),
        profile_resolver=lambda:profile,store_factory=lambda p:CodexQuotaStore(p,root=data/'quota-fixture'))
    return quota_host.HostAdapter(store,source,quota_host.Bindings(worker))


quota_host.production=owned
runpy.run_path(str(ROOT/'tests/support/automation_http_server.py'),run_name='__main__')
