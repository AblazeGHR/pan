"""Own profile/CLI only. Production host IO factory replaced before server import."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

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

# The canonical production Service intentionally keeps its default unavailable
# adapter. Merely replacing the factory no longer binds this owned read fixture.
# Bind only this fixture, leaving every business mutation port unbound.
from packages.automation.service import Service
original_init=Service.__init__


def initialize(self, root, worker, pause):
    original_init(self,root,worker,pause)
    data=Path(os.environ['PAN_E2E_DATA_ROOT']).resolve()
    assert Path(root).resolve()==data/'automation'
    policy=self.quota_policy
    diagnostic=data/'host-binding.jsonl'
    def record(phase,**fields):
        with diagnostic.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'phase':phase,'pid':os.getpid(),'at':time.time(),**fields})+'\n')
    record('before-binding',adapter=type(policy.adapter).__name__)
    adapter=quota_host.production(policy.store,worker,data)
    assert not adapter.ports
    adapter.runtime=policy
    sample=adapter.sample
    async def observed_sample():
        record('sample-start')
        try:
            value=await sample()
        except Exception as exc:
            record('sample-error',errorType=type(exc).__name__)
            raise
        record('sample-end',providerStatus=value['provider']['status'],nativeStatus=value['native']['status'])
        return value
    adapter.sample=observed_sample
    policy.adapter=adapter
    record('bound-readonly',adapter=type(adapter).__name__,mutationPorts=len(adapter.ports))


Service.__init__=initialize
runpy.run_path(str(ROOT/'tests/support/automation_http_server.py'),run_name='__main__')
