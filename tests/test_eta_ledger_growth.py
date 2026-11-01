"""Finite ledger growth measurement; not a capacity or retention guarantee."""
import asyncio
import json
from pathlib import Path
import time

from packages.actions.store import atomic
from packages.automation.store import Ledger, Conflict
import pytest
from tests.test_automation_core import async_test, trigger, event


@async_test
async def test_owned_ledger_growth_measurement(tmp_path):
    samples=[]
    for size in (1,100,1000):
        ledger=Ledger(tmp_path/str(size))
        ledger.put({**trigger(),'enabled':False},0)
        state=ledger.read()
        state['runs']={f'r{i}':dict(id=f'r{i}',revision=1,triggerId='t',triggerRevision=1,
            actionId='pause',event=event(f'e{i}'),status='succeeded',arguments={},
            result={'ok':True},targetIds=['target'],targetResults=[{'sessionId':'target','ok':True}]) for i in range(size)}
        await asyncio.to_thread(atomic,ledger.path,state)
        start=time.perf_counter()
        state=await asyncio.to_thread(ledger.read)
        read_ms=(time.perf_counter()-start)*1000
        before=ledger.path.read_bytes()
        start=time.perf_counter()
        with pytest.raises(Conflict, match='read only'):
            await asyncio.to_thread(ledger.update,'r0',note='owned growth probe')
        refusal_ms=(time.perf_counter()-start)*1000
        assert ledger.path.read_bytes()==before
        assert len(state['runs'])==size
        samples.append(dict(runs=size,bytes=ledger.path.stat().st_size,readMs=read_ms,readOnlyRefusalMs=refusal_ms))
    assert samples[0]['bytes']<samples[-1]['bytes']
    out=tmp_path/'evidence';out.mkdir(parents=True,exist_ok=True)
    (out/'ledger-growth.json').write_text(json.dumps({'samples':samples,'retention':'Legacy history is preserved read only; canonical retention belongs to Jobs; finite synthetic sample only, not a capacity guarantee'},indent=2),encoding='utf-8')
