"""Opt-in instrumentation for the two unchanged CI nodes; not a global plugin."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import time

import pytest


def _output():
    value = os.environ.get("CPX1_DIAGNOSTIC_DIR")
    if not value:
        raise RuntimeError("CPX1_DIAGNOSTIC_DIR must be a fresh owned directory")
    path = Path(value).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(autouse=True)
def _instrument_cpx1_nodes(request, monkeypatch, tmp_path):
    name = request.node.name
    if name in {"test_stream_persists_before_result_then_idle_and_does_not_wait_for_enrich", "test_terminal_finishes_while_enrichment_is_held"}:
        from packages.core import worker
        test = request.module if name.startswith("test_stream_") else request.module.original

        origin = time.monotonic_ns()
        records = []
        lock = threading.Lock()

        def mark(phase):
            with lock:
                records.append({"phase": phase, "offset_ns": time.monotonic_ns() - origin,
                                "thread": "main" if threading.current_thread() is threading.main_thread() else "worker"})

        def handoff(label):
            mark(label + ".queued")
            asyncio.get_running_loop().call_soon(mark, label + ".delivered")

        def wrap_async(obj, attr):
            original = getattr(obj, attr)
            async def wrapper(*args, **kwargs):
                mark(attr + ".enter")
                try:
                    return await original(*args, **kwargs)
                finally:
                    mark(attr + ".exit")
            monkeypatch.setattr(obj, attr, wrapper)

        for attr in ("_read_stdout", "_flush_history_now", "_persist_terminal_state", "_run_usage_enrichment", "_detect_offline_exit", "_kill_process_tree", "_enqueue_zombie_report", "_record_worker_crash"):
            wrap_async(worker, attr)
        def wrap_sync(obj, attr):
            original = getattr(obj, attr)
            def wrapper(*args, **kwargs):
                mark(attr + ".enter")
                try:
                    return original(*args, **kwargs)
                finally:
                    mark(attr + ".exit")
            monkeypatch.setattr(obj, attr, wrapper)
        for attr in ("_cleanup_system_prompt_file", "_runtime_stopped", "_unregister_worker"):
            wrap_sync(worker, attr)
        orig_bcast = worker._bcast
        async def bcast(event):
            if event.get("type") in {"worker.result", "worker.status"}:
                mark("broadcast." + event.get("type", "") + "." + str(event.get("status", "")))
                handoff("broadcast.handoff")
            return await orig_bcast(event)
        monkeypatch.setattr(worker, "_bcast", bcast)
        original_read = test._Process.read
        async def read(process, *args, **kwargs):
            value = await original_read(process, *args, **kwargs)
            if not value:
                mark("stdout.eof")
                handoff("eof.handoff")
            return value
        monkeypatch.setattr(test._Process, "read", read)
        original_to_thread = asyncio.to_thread
        async def to_thread(func, *args, **kwargs):
            if getattr(func, "__name__", "") not in {"slow_enrich", "held_enrichment"}:
                return await original_to_thread(func, *args, **kwargs)
            mark("enrich.dispatch")
            handoff("enrich.handoff")
            def observed():
                mark("enrich.thread.enter")
                try:
                    return func(*args, **kwargs)
                finally:
                    mark("enrich.thread.exit")
            try:
                return await original_to_thread(observed)
            finally:
                mark("enrich.await.return")
        monkeypatch.setattr(asyncio, "to_thread", to_thread)
        try:
            yield
        finally:
            filename = "terminal-phases.json" if name.startswith("test_stream_") else "terminal-held-phases.json"
            (_output() / filename).write_text(json.dumps({"clock": "monotonic_ns", "events": records}, indent=2), encoding="utf-8")
    elif name == "test_locker_order_scroll_live":
        test = request.module
        (_output() / "locker-requests.json").write_text(json.dumps({"state": "browser_not_started", "requests": [], "samples": []}), encoding="utf-8")
        from tests import test_real_queue_http_e2e as runtime_fixture
        launcher = test.ROOT / "tests/support/cpx1_locker_timed_server.py"
        original_start = runtime_fixture._PanRuntime.start
        def timed_start(runtime):
            monkeypatch.setattr(runtime_fixture, "SERVER_LAUNCHER", launcher)
            return original_start(runtime)
        monkeypatch.setattr(runtime_fixture._PanRuntime, "start", timed_start)
        source = test.ROOT / "packages/web/e2e/locker-order-scroll.mjs"
        raw = source.read_text(encoding="utf-8")
        marker = "const timeline=page.getByRole('region',{name:'Locker messages'});"
        if raw.count(marker) != 1:
            raise RuntimeError("original browser script instrumentation anchor changed")
        injection = r'''
const diagStart=performance.now(), diagWallStartMs=Date.now(), diagRequests=[], diagByRequest=new WeakMap(), diagSamples=[];
let uiTransitionsBeforeReload=[];
const elapsed=()=>Math.round((performance.now()-diagStart)*1000)/1000;
page.on('request',r=>{
  const u=new URL(r.url()), m=u.pathname.match(/^\/api\/(user-locker-messages|user-inbox|reminding-cards)\/([^/]+)\/read$/);
  if(r.method()!=='PUT'||!m)return;
  const row={kind:m[1],id:decodeURIComponent(m[2]),requestMs:elapsed(),responseMs:null,finishedMs:null,status:null,failed:false};
  diagRequests.push(row);diagByRequest.set(r,row);
});
page.on('response',r=>{const row=diagByRequest.get(r.request());if(row){row.responseMs=elapsed();row.status=r.status();}});
page.on('requestfinished',r=>{const row=diagByRequest.get(r);if(row)row.finishedMs=elapsed();});
page.on('requestfailed',r=>{const row=diagByRequest.get(r);if(row){row.failed=true;row.responseMs=elapsed();}});
let sampling=false;
const timer=setInterval(async()=>{
  if(sampling||diagSamples.length>=100)return; sampling=true;
  try { const counts=await page.evaluate(()=>{const n=document.querySelector('[aria-label="Locker messages"]');
    return n?{ordinary:n.querySelectorAll('article button[aria-label="Mark read"]').length,
      cards:n.querySelectorAll('article button[aria-label="Mark card as read"]').length}:null});
    if(counts)diagSamples.push({ms:elapsed(),...counts});
  } catch {} finally {sampling=false;}
},250);
'''
        raw = raw.replace(marker, injection + "\n" + marker)
        goto = "  await page.goto(base+'/react/locker');"
        if raw.count(goto) != 1:
            raise RuntimeError("original browser navigation anchor changed")
        raw = raw.replace(goto, goto + r'''
  await page.evaluate(() => {
    window.__cpx1ReadTransitions=[];
    new MutationObserver(changes=>{
      for(const change of changes){
        if(change.type!=='attributes')continue;
        const button=change.target, label=button.getAttribute('aria-label');
        if(label!=='Mark unread'&&label!=='Mark card as unread')continue;
        const article=button.closest('article');
        const id=article?.getAttribute('data-locker-message-id')||article?.getAttribute('data-card-id');
        if(id&&window.__cpx1ReadTransitions.length<512)
          window.__cpx1ReadTransitions.push({id,wallMs:Date.now(),label});
      }
    }).observe(document,{subtree:true,attributes:true,attributeFilter:['aria-label']});
  });
''')
        reload_marker = "  await page.reload();"
        if raw.count(reload_marker) != 1:
            raise RuntimeError("original browser reload anchor changed")
        raw = raw.replace(reload_marker, "  uiTransitionsBeforeReload=await page.evaluate(()=>window.__cpx1ReadTransitions||[]);\n" + reload_marker)
        final = "finally {await fs.writeFile(out+'/scroll-browser.json'"
        if raw.count(final) != 1:
            raise RuntimeError("original browser script finally anchor changed")
        raw = raw.replace(final, "finally {clearInterval(timer); let uiTransitions=uiTransitionsBeforeReload; if(!uiTransitions.length)try {uiTransitions=await page.evaluate(()=>window.__cpx1ReadTransitions||[]);} catch {} await fs.writeFile(process.env.CPX1_DIAGNOSTIC_DIR+'/locker-requests.json',JSON.stringify({wallStartMs:diagWallStartMs,expectedIds:f.ids,requests:diagRequests,samples:diagSamples,uiTransitions,counts:{issued:diagRequests.length,responses:diagRequests.filter(r=>r.status!==null).length,finished:diagRequests.filter(r=>r.finishedMs!==null).length,failed:diagRequests.filter(r=>r.failed||(r.status!==null&&r.status>=400)).length}},null,2)); await fs.writeFile(out+'/scroll-browser.json'")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".mjs", prefix="cpx1-diagnostic-", dir=source.parent, delete=False) as file:
            file.write(raw)
            script = Path(file.name)
        original_run = test.subprocess.run
        def run(args, *a, **kw):
            if isinstance(args, list) and len(args) > 1 and str(args[1]) == str(source):
                args = list(args)
                args[1] = str(script)
            return original_run(args, *a, **kw)
        monkeypatch.setattr(test.subprocess, "run", run)
        monkeypatch.setattr(test, "OUT", tmp_path / "owned-output")
        try:
            yield
        finally:
            script.unlink(missing_ok=True)
            proof_path = test.OUT / "evidence.json"
            proof = json.loads(proof_path.read_text(encoding="utf-8")) if proof_path.exists() else {}
            (_output() / "locker-cleanup.json").write_text(json.dumps({"browserExitCode": proof.get("browserExitCode"), "portReleased": proof.get("portReleased")}, indent=2), encoding="utf-8")
    else:
        yield
