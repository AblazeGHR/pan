"""Default hidden command windows, explicit opt-out, and old-record policy."""
import asyncio
import json
import os
import subprocess
import sys
from unittest.mock import Mock, MagicMock
from pathlib import Path

import pytest

from packages.jobs.window import command_window_options, validate_hide_window, windows_creation_flags
from tests.test_job_automatic_recovery import env  # noqa: F401


@pytest.mark.parametrize("value", [None, "true", "false", 0, 1, [], {}])
def test_window_policy_rejects_non_boolean(value):
    with pytest.raises(ValueError):
        validate_hide_window(value)


@pytest.mark.parametrize("hidden", [True, False])
def test_flags_never_combine_new_console_with_no_window(hidden):
    assert windows_creation_flags(hidden) == (0x08000000 if hidden else 0x10)
    options = command_window_options(hidden)
    if os.name == "nt":
        assert options["startupinfo"].wShowWindow == (0 if hidden else 1)
        assert options["creationflags"] == windows_creation_flags(hidden)
    else:
        assert options == {}


def test_non_windows_options_have_no_windows_only_kwargs(monkeypatch):
    from packages.jobs import window
    monkeypatch.setattr(window, "os", Mock(name="posix"))
    window.os.name = "posix"
    assert window.command_window_options() == {}
    assert window.command_window_options(False) == {}


@pytest.mark.parametrize("shell", [False, True])
@pytest.mark.parametrize("hidden", [None, True, False])
def test_legacy_runner_uses_policy_for_argv_shell_and_old_records(tmp_path, shell, hidden):
    from packages.actions.typed_executor import TypedExecutor
    from tests.test_job_final_executors import FakeOwner,attempt,key
    # The typed successor carries a shell invocation as explicit argv. It
    # never resurrects raw shellCommand/Popen(shell=True) dispatch.
    argv=([os.environ.get('COMSPEC','cmd.exe'),'/d','/c','echo owned'] if os.name=='nt'
        else ['/bin/sh','-c','printf owned']) if shell else [sys.executable,'-c','pass']
    arguments={'argv':argv,'cwd':str(tmp_path)}
    if hidden is not None:arguments['hideWindow']=hidden
    owner=FakeOwner()
    result=asyncio.run(TypedExecutor(process_owner=owner).execute(attempt=attempt(),effect_key=key(),
        frozen_plan={'operation':'process','arguments':arguments}))
    assert result['outcome']=='succeeded' and result['cleanupProof']['confirmed'] is True
    assert len(owner.calls)==1
    actual,options=owner.calls[0]
    assert actual==argv and options['cwd']==str(tmp_path)
    assert options['hide_window'] is (hidden is not False)
    assert options['stdin']==subprocess.DEVNULL and options.get('shell',False) is False


def test_mcp_and_http_forward_explicit_visible_option(monkeypatch,env):
    from packages.mcp import server as mcp
    from tests.test_job_automatic_recovery import job_definition
    calls=[]
    monkeypatch.setattr(mcp,'_caller_identity',lambda:{'id':'ses_self'})
    monkeypatch.setattr(mcp,'_check_access',lambda *args,**kwargs:None)
    monkeypatch.setattr(mcp,'_api',lambda method,path,body:calls.append(body) or body)
    assert mcp.agent_background_start(['python'],'.',hide_window=False,name='Validate window policy')['hideWindow'] is False
    assert calls[0]['hideWindow'] is False
    value=job_definition(env,{'automatic':False})
    value['steps'][0]['arguments']['hideWindow']={'from':'literal','value':False}
    response=env.client.post('/api/jobs',json={'definition':value,'expectedRevision':0,'requestKey':'visible'})
    assert response.status_code==201,response.text
    item=response.json()['result']['object']
    assert item['steps'][0]['arguments']['hideWindow']['value'] is False
    assert env.client.get('/api/jobs/'+item['id']).json()['steps'][0]['arguments']['hideWindow']['value'] is False
    state=env.service.final.get(item['id'])
    assert not state['runs'] and not state['attempts'] and not state['effects']


def test_independent_service_persists_and_retries_visible_policy(tmp_path):
    from packages.jobs.independent_executor import ExecutionStore,ExecutionHost
    from packages.actions.typed_executor import EffectReceipt
    from tests.test_job_final_independent_execution import attempt,key,process_plan
    store=ExecutionStore(tmp_path/'executions');calls=[];host=ExecutionHost(store,launch=lambda *args:calls.append(args))
    plan={**process_plan(tmp_path),'hideWindow':False}
    request={'jobId':'job_visible','attempt':attempt(),'effectKey':key(),'frozenPlan':plan}
    original=host.submit_execution(request)
    assert original['frozenPlan']['hideWindow'] is False and len(calls)==1
    claimed=store.claim(original['effectId'],attempt(),{'pid':7,'birth':'trusted-original'})
    failure=EffectReceipt(key(),'rejected',{'exitCode':7},{'confirmed':True}).as_dict()
    store.finish(original['effectId'],attempt(),claimed['claim'],failure)
    retried=host.submit_execution({**request,'retry':True,'attempt':attempt(attemptId='attempt_retry',generation=2)})
    assert retried['effectId']==original['effectId'] and retried['effectKey']==original['effectKey']
    assert retried['frozenPlan']==original['frozenPlan'] and len(calls)==2
    assert retried['history'][0]['result']==failure
    before=store.get(original['effectId'])
    with pytest.raises(ValueError,match='invalid typed process arguments'):
        host.submit_execution({**request,'frozenPlan':{**plan,'hideWindow':'false'}})
    assert store.get(original['effectId'])==before and len(calls)==2


def test_independent_old_payload_idempotency_does_not_break(tmp_path):
    from packages.jobs.independent_executor import ExecutionStore,ExecutionHost,ExecutionConflict
    from tests.test_job_final_independent_execution import attempt,key,process_plan
    store=ExecutionStore(tmp_path/'executions');calls=[];host=ExecutionHost(store,launch=lambda *args:calls.append(args))
    request={'jobId':'job_original','attempt':attempt(),'effectKey':key(),'frozenPlan':process_plan(tmp_path)}
    original=host.submit_execution(request)
    assert 'hideWindow' not in original['frozenPlan']
    cold=ExecutionHost(ExecutionStore(store.root),launch=lambda *args:pytest.fail('must query original'))
    assert cold.submit_execution(request)==original
    assert cold.submit_execution({**request,'retry':True})==original
    assert cold.get_execution(original['effectId'])==original and len(calls)==1
    # A changed plan is not a historical default and cannot steal an effect.
    with pytest.raises(ExecutionConflict):
        cold.submit_execution({**request,'frozenPlan':{**request['frozenPlan'],'hideWindow':True}})
    assert cold.get_execution(original['effectId'])==original


def test_shell_normalization_keeps_explicit_visibility_and_rejects_string(tmp_path):
    from packages.jobs.api import _normalize_scheduled_action as normalize_scheduled_action
    action = {"api": "shell", "args": {"command": "echo owned", "cwd": str(tmp_path), "hideWindow": False}}
    normalized, error = normalize_scheduled_action(action)
    assert error is None and normalized["args"]["hideWindow"] is False
    _, error = normalize_scheduled_action({**action, "args": {**action["args"], "hideWindow": "false"}})
    assert error is not None


@pytest.mark.skipif(os.name != "nt", reason="Windows console policy")
@pytest.mark.parametrize("hidden", [True, False])
def test_real_windows_child_console_allocation(hidden):
    # Only this owned, short-lived child. No window enumeration or external kill.
    program = "import ctypes,json; print(json.dumps({'console': bool(ctypes.windll.kernel32.GetConsoleWindow())}))"
    result = subprocess.run([sys.executable, "-c", program], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=10,
                            **command_window_options(hidden))
    assert result.returncode == 0
    assert json.loads(result.stdout)["console"] is (not hidden)


@pytest.mark.skipif(os.name != "nt", reason="Windows console/window APIs")
@pytest.mark.parametrize("hidden", [True, False])
def test_owned_python_tree_window_visibility(tmp_path, hidden):
    from ctypes import wintypes
    from packages.core.terminal.identity import read_creation_filetime, kernel32
    script = Path(__file__).parent / "helpers" / "window_tree_probe.py"
    process = subprocess.Popen([sys.executable, str(script.resolve()),
                                "hidden" if hidden else "visible", "2"],
                               cwd=tmp_path, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               **command_window_options(hidden))
    handle = int(process._handle)
    filetime = read_creation_filetime(handle)
    assert filetime is not None
    try:
        output, error = process.communicate(timeout=30)
        assert process.returncode == 0, error.decode("utf-8", "replace")
        evidence = json.loads(output)
        print("WINDOW_TREE_EVIDENCE " + json.dumps({"hidden": hidden,
              "launchPid": process.pid, "launchFiletime": str(filetime), "tree": evidence}))
        for depth in (2, 1, 0):
            assert evidence["depth"] == depth
            assert evidence["filetime"].isdigit()
            assert bool(evidence["consoleHwnd"]) is (not hidden)
            assert evidence["visible"] is (not hidden)
            if depth:
                assert evidence["childLaunch"]["exitCode"] == 0
                evidence = evidence["child"]
    finally:
        if process.poll() is None:
            kernel = kernel32()
            assert kernel.GetProcessId(wintypes.HANDLE(handle)) == process.pid
            assert read_creation_filetime(handle) == filetime
            assert kernel.TerminateProcess(wintypes.HANDLE(handle), 1)
            process.wait(timeout=5)


@pytest.mark.parametrize("relative", ["codex/wrapper.py", "codex/app_server_wrapper.py",
                                    "kimi/wrapper.py", "opencode/wrapper.py"])
def test_bridge_filename_entrypoint_imports_policy_outside_repo(tmp_path, relative):
    repo = Path(__file__).resolve().parents[1]
    script = repo / "packages" / "core" / "adapters" / relative
    env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path,
                            env=env, capture_output=True, timeout=10,
                            **command_window_options())
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert b"usage:" in result.stdout


def test_isolated_entrypoint_preserves_output_and_exit_code(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    program = "import sys; print('owned stdout', flush=True); print('owned stderr', file=sys.stderr); sys.exit(7)"
    result = subprocess.run([sys.executable, str(repo / "scripts/run_tests_isolated.py"),
                             "--", sys.executable, "-c", program], cwd=tmp_path,
                            capture_output=True, timeout=10, **command_window_options())
    assert result.returncode == 7
    assert b"owned stdout" in result.stdout
    assert b"owned stderr" in result.stderr


def test_kimi_wrapper_help_preserves_unicode_with_cp1252_stdout(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    script = repo / "packages" / "core" / "adapters" / "kimi" / "wrapper.py"
    env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    env.update(PYTHONUTF8="0", PYTHONIOENCODING="cp1252")
    result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path,
                            env=env, capture_output=True, timeout=10,
                            **command_window_options())
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    help_text = result.stdout.decode("utf-8")
    assert "usage:" in help_text
    assert "worker 注入的系统提示词（首轮转 --agent-file）" in help_text
