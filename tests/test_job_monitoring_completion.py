"""Canonical monitoring completion proof on the detached execution path."""
from __future__ import annotations

import time
import sys
import copy
import asyncio
import json
import socket
import threading
from pathlib import Path

import pytest
import httpx
import uvicorn

from packages.jobs.independent_executor import ExecutionHost, ExecutionStore, ExecutionConflict, fence, monitoring_proof_valid
from packages.jobs.final_service import FinalService, definition, object_ref
from packages.jobs.final_runtime.ledger import aggregate
from packages.jobs.independent_execution_runner import _monitoring_completion
from tests.test_job_automatic_recovery import env, USER, create, job_definition, policy, state


def attempt():
    return {"attemptId": "attempt_monitor", "runId": "run_monitor", "stepId": "step_monitor",
            "leaseId": "lease_monitor", "ownerEpoch": 1, "generation": 1, "state": "running"}


def key():
    return {"runId": "run_monitor", "stepId": "step_monitor", "targetId": "monitor", "operationOrdinal": 0}


def wait_for(store, identity):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        doc = store.get(identity)
        if doc["result"] is not None:
            return doc
        time.sleep(.05)
    raise AssertionError("detached monitoring execution did not settle")


def submit(tmp_path, script):
    store = ExecutionStore(tmp_path / "host")
    plan = {"operation": "process", "argv": [sys.executable, "-c", script],
            "cwd": str(tmp_path),
            "completion": {"contract": "monitoring", "parameters": {"signalType": "owned.done"}}}
    doc = ExecutionHost(store).submit_execution({"jobId": "job_monitor", "attempt": attempt(),
        "effectKey": key(), "frozenPlan": plan})
    return store, wait_for(store, doc["effectId"])


def test_detached_monitoring_proof_is_bounded_and_attempt_bound(tmp_path):
    store, final = submit(tmp_path, "import json,sys; print(json.dumps({'panJobCompletion':{'type':'owned.done','completed':True},'output':'kept'})); print('stderr-only',file=sys.stderr)")
    result = final["result"]
    assert result["outcome"] == "succeeded"
    proof = result["receipt"]["completionProof"]
    assert proof["source"] == "stdout" and proof["encoding"] == "utf-8"
    assert fence(proof["attempt"]) == fence(attempt())
    assert proof["effectKey"] == key()
    assert proof["signal"] == {"type": "owned.done", "completed": True}
    assert result['receipt']['structuredContent']['output']=='kept'
    assert proof["bytes"] <= 512 * 1024
    assert final["frozenPlan"]["completion"]["parameters"]["signalType"] == "owned.done"
    assert store.events()["events"][0]["attempt"] == attempt()
    log = store.read_execution_log(final["effectId"], attempt())
    assert 'panJobCompletion' in log['text'] and 'stderr-only' in log['text']
    assert monitoring_proof_valid(result, {**attempt(), 'state':'uncertain'}, final['frozenPlan']['completion'])
    for field, value in [('attemptId','stale'), ('leaseId','stale'), ('generation',2), ('ownerEpoch',2)]:
        bad = copy.deepcopy(result)
        bad['receipt']['completionProof']['attempt'][field] = value
        assert not monitoring_proof_valid(bad, attempt(), final['frozenPlan']['completion'])
    bad = copy.deepcopy(result)
    bad['receipt']['completionProof']['effectKey']['targetId'] = 'another'
    assert not monitoring_proof_valid(bad, attempt(), final['frozenPlan']['completion'])
    for mutate in [lambda r: r['receipt']['completionProof'].update(bytes=512*1024+1),
                   lambda r: r['cleanupProof'].update(confirmed=False),
                   lambda r: r['cleanupProof']['ownedTree'].update(remaining=[7])]:
        bad = copy.deepcopy(result)
        mutate(bad)
        assert not monitoring_proof_valid(bad, attempt(), final['frozenPlan']['completion'])


@pytest.mark.parametrize('script', [
    'pass',
    "import json; print(json.dumps({'panJobCompletion':{'type':'other.done','completed':True}}))",
    "import json; print(json.dumps({'panJobCompletion':{'type':'owned.done','completed':False}}))",
    "import json; print(json.dumps({'panJobCompletion':{'type':'owned.done','completed':True,'extra':1}}))",
    "import sys,json; print(json.dumps({'panJobCompletion':{'type':'owned.done','completed':True}}),file=sys.stderr)",
    "import sys; sys.stdout.buffer.write(b'\\xff')",
    "print('x' * (512 * 1024 + 1))",
    "print('{}\\n{}')",
])
def test_detached_monitoring_missing_or_mismatched_signal_is_explicit_rejection(tmp_path, script):
    _, final = submit(tmp_path, script)
    result = final["result"]
    assert result["outcome"] == "rejected"
    assert result["receipt"]["code"] == "monitoring_completion_invalid"
    assert "completionProof" not in result["receipt"]
    assert result["effectKey"] == key()
    assert result['cleanupProof']['confirmed'] is True
    assert 'executionTiming' in result['receipt'] and 'logRef' in result['receipt']


def test_matching_signal_never_overrides_nonzero_exit(tmp_path):
    _, final = submit(tmp_path, "import json; print(json.dumps({'panJobCompletion':{'type':'owned.done','completed':True}})); raise SystemExit(7)")
    assert final['result']['outcome']=='rejected'
    assert final['result']['receipt']['exitCode']==7
    assert 'completionProof' not in final['result']['receipt']


def test_output_limit_uses_original_owned_cleanup_before_child_wait_ends(tmp_path):
    started = time.monotonic()
    _, final = submit(tmp_path, "import sys,time; sys.stdout.write('x'*(512*1024+1)); sys.stdout.flush(); time.sleep(10)")
    assert time.monotonic()-started < 8
    result = final['result']
    assert result['outcome']=='rejected'
    assert result['receipt']['code']=='monitoring_completion_invalid'
    assert result['cleanupProof']['reason']=='monitoring-output-limit'
    assert result['cleanupProof']['ownedTree']['confirmed'] is True
    assert result['cleanupProof']['ownedTree']['remaining']==[]


@pytest.mark.parametrize('parameters', [None, {}, {'signalType':''}, {'signalType':True}, {'signalType':'x'*129}])
def test_invalid_monitoring_contract_has_no_admission_or_launch(tmp_path, parameters):
    store = ExecutionStore(tmp_path)
    launches = []
    host = ExecutionHost(store, launch=lambda *args: launches.append(args))
    with pytest.raises(ValueError):
        host.submit_execution({'jobId':'job_monitor','attempt':attempt(),'effectKey':key(),
            'frozenPlan':{'operation':'process','argv':[sys.executable,'-c','pass'],'cwd':str(tmp_path),
                          'completion':{'contract':'monitoring','parameters':parameters}}})
    assert launches==[] and store.events()=={'events':[]}


@pytest.mark.parametrize('outcome', ['unknown','partial','succeeded'])
def test_store_never_promotes_unknown_or_accepts_unproven_success(tmp_path, outcome):
    store = ExecutionStore(tmp_path)
    plan = {'operation':'process', 'completion':{'contract':'monitoring','parameters':{'signalType':'owned.done'}}}
    doc, _ = store.admit(job_id='job_monitor', attempt=attempt(), effect_key=key(), frozen_plan=plan)
    doc = store.claim(doc['effectId'], attempt(), {'pid':7,'birth':'proof'})
    receipt = {'effectKey':key(),'outcome':outcome,'receipt':{'panJobCompletion':{'type':'owned.done','completed':True}}}
    final = store.finish(doc['effectId'], attempt(), doc['claim'], receipt)
    assert final['result']['outcome'] == ('unknown' if outcome == 'succeeded' else outcome)
    assert store.finish(doc['effectId'], attempt(), doc['claim'], receipt) == final
    with pytest.raises(ExecutionConflict):
        store.admit(job_id='job_monitor', attempt={**attempt(),'attemptId':'retry','generation':2},
                    effect_key=key(), frozen_plan=plan, retry=True)


@pytest.mark.parametrize('outcome', ['unknown','partial'])
def test_runner_never_promotes_unknown_even_with_valid_stdout(outcome):
    class Output:
        def finish(self):
            raw = b'{"panJobCompletion":{"type":"owned.done","completed":true}}'
            return raw, len(raw)
    result = {'effectKey':key(),'outcome':outcome,'receipt':{'code':'original_unknown'}}
    assert _monitoring_completion(copy.deepcopy(result), Output(), attempt=attempt(),effect_key=key(),
        completion={'contract':'monitoring','parameters':{'signalType':'owned.done'}})==result


def monitoring_definition(h, code):
    action = next(a for a in h.service.actions(actor=USER)['items'] if a['id']=='product_process_cleanup_v1')
    value = definition('action', action)
    value['completionContracts'].append({'id':'monitoring','schema':{'type':'object'},'terminalProofSchema':{'type':'object'}})
    action = h.service.publish_action('owned_completion_monitor', value)
    body = job_definition(h, policy(stableResetSeconds='2',backoffSeconds=['0.1']), code=code)
    body['steps'][0]['actionRef'] = object_ref('action', action)
    body['steps'][0]['completion'] = {'contract':'monitoring','parameters':{'signalType':'owned.done'}}
    return body


def test_owned_http_detached_monitoring_retry_cold_reopen_and_actual_pulse(env):
    sock = socket.socket()
    sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]
    assert port not in (8767,8768)
    server = uvicorn.Server(uvicorn.Config(env.app,lifespan='off',log_level='error'))
    thread = threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
    thread.start()
    deadline = time.monotonic()+10
    while not server.started:
        assert time.monotonic()<deadline
        time.sleep(.02)
    client = httpx.Client(base_url=f'http://127.0.0.1:{port}',headers=dict(env.client.headers))
    prior = env.client
    env.client = client
    counter = env.tmp/'monitor-count'
    code = (f"from pathlib import Path; import time,json,sys; p=Path({str(counter)!r}); n=int(p.read_text()) if p.exists() else 0; "
            "p.write_text(str(n+1)); time.sleep(2.2 if n==1 else .05); print(json.dumps({'panJobCompletion':{'type':'owned.done','completed':n>1}})); print('stderr-is-log',file=sys.stderr)")
    try:
        jid,rid = create(env,monitoring_definition(env,code))
        async def run():
            await env.service.dispatch_once()
            frozen = copy.deepcopy(env.service.final.store.get(jid)['finalRuntime']['plans'][rid]['frozen'])
            await env.service.close()
            restored = FinalService(env.root,env.auth)
            env.service = restored
            env.app.state.job_final_service = restored
            restored.recover()
            await restored.start()
            try:
                deadline = time.monotonic()+20
                while state(env,jid)['runs'][rid]['state'] != 'completed':
                    assert time.monotonic()<deadline, state(env,jid)['runs'][rid]
                    await asyncio.sleep(.03)
            finally:
                await restored.close()
            result = state(env,jid)
            assert counter.read_text()=='3'
            assert len(result['attempts'])==3 and len(result['runs'])==1
            assert sorted(a['generation'] for a in result['attempts'].values())==[1,2,3]
            assert len({json.dumps(e['effectKey'],sort_keys=True) for e in result['effects']})==1
            assert result['effects'][0]['receipt']['receipt']['code']=='monitoring_completion_invalid'
            proof = result['effects'][-1]['receipt']['receipt']['completionProof']
            assert proof['signal']=={'type':'owned.done','completed':True}
            assert proof['attempt']['attemptId']==result['effects'][-1]['attemptId']
            assert env.service.final.store.get(jid)['finalRuntime']['plans'][rid]['frozen']==frozen
            window = env.service.final.store.get(jid)['finalRuntime']['plans'][rid]['recoveryWindows']['s0']
            assert window['baseGeneration']==1
            output = {'transport':'owned HTTP','executor':'default detached','pulse':'FinalService.start',
                      'coldReopen':True,'invocations':3,'attemptGenerations':[1,2,3], 'runCount':1,
                      'maxAttempts':2,'stableWindowBaseGeneration':1,
                      'distinctEffectKeys':1,'firstOutcome':'rejected','finalOutcome':'succeeded',
                      'completion':frozen['steps'][0]['completion'],'proofSource':proof['source']}
            Path('.validation').mkdir(exist_ok=True)
            Path('.validation/monitoring-detached.json').write_text(json.dumps(output,indent=2)+'\n')
        asyncio.run(run())
        assert client.get('/api/job-runs/'+rid).json()['state']=='completed'
    finally:
        env.client = prior
        client.close()
        server.should_exit=True
        thread.join(8)
        sock.close()
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1',port)) != 0


def test_original_unknown_reconciles_proof_once_without_retry(env):
    code = "import time,json; time.sleep(.3); print(json.dumps({'panJobCompletion':{'type':'owned.done','completed':True}}))"
    jid,rid = create(env,monitoring_definition(env,code))
    asyncio.run(env.service.dispatch_once())
    with env.service.transaction() as db:
        job = env.service._get(db,'job',jid)
        raw = aggregate(job,env.root)
        aid = raw['plans'][rid].pop('pendingProcessAttempt')
        env.service.final.ledger.finish_attempt(job,env.root,aid,'uncertain')
        env.service.final.ledger.record_effect(job,env.root,aid,'uncertain',{'code':'lost_ack'})
        env.service.final.ledger.transition_run(job,env.root,rid,'uncertain')
        env.service._save(db,'job',jid,job)
    async def reconcile():
        deadline = time.monotonic()+12
        while state(env,jid)['runs'][rid]['state']!='completed':
            assert time.monotonic()<deadline
            await env.service.recover_external_receipts()
            await asyncio.sleep(.03)
        await env.service.recover_external_receipts()
    asyncio.run(reconcile())
    raw = env.service.final.store.get(jid)['finalRuntime']
    assert len(raw['attempts'])==1 and len(raw['effects'])==1
    assert raw['effects'][0]['attemptId']==aid and raw['effects'][0]['outcome']=='succeeded'
    assert len(raw['receiptReconciliations'][aid])==1
    assert raw['receiptReconciliations'][aid][0]['outcome']=='unknown'
