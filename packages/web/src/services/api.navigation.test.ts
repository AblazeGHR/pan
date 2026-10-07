// @vitest-environment jsdom
import { performance } from 'node:perf_hooks';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { fetchSessionNavigation } from './api';
import { beginForegroundRequest, foregroundActivity } from './foregroundActivity';
beforeEach(()=>vi.stubGlobal('performance',performance));
afterEach(()=>vi.unstubAllGlobals());
const page=()=>new Response(JSON.stringify({history:[],start:0,total:0}));
describe('navigation yields to foreground history',()=>{
  it('waits for an active body request and never counts navigation as foreground',async()=>{
    const fetch=vi.fn().mockResolvedValue(page());vi.stubGlobal('fetch',fetch);
    const finish=beginForegroundRequest();
    const pending=fetchSessionNavigation('session');
    await Promise.resolve();expect(fetch).not.toHaveBeenCalled();finish();
    await pending;expect(fetch).toHaveBeenCalledTimes(1);expect(foregroundActivity().requests).toBe(0);
  });
  it('cancels while waiting, without dispatching a request after the foreground completes',async()=>{
    const fetch=vi.fn().mockResolvedValue(page());vi.stubGlobal('fetch',fetch);
    const finish=beginForegroundRequest(),controller=new AbortController();
    const pending=fetchSessionNavigation('session',0,500,controller.signal);controller.abort();
    await expect(pending).rejects.toMatchObject({name:'AbortError'});finish();
    await Promise.resolve();expect(fetch).not.toHaveBeenCalled();
  });
  it('holds compact decoding when foreground work starts during an in-flight navigation read',async()=>{
    let respond!:(response:Response)=>void;
    vi.stubGlobal('fetch',vi.fn(()=>new Promise<Response>(resolve=>{respond=resolve;})));
    let settled=false;const pending=fetchSessionNavigation('session').then(()=>{settled=true;});
    await vi.waitFor(()=>expect(respond).toBeDefined());const finish=beginForegroundRequest();respond(page());
    await Promise.resolve();await Promise.resolve();expect(settled).toBe(false);
    finish();await pending;expect(settled).toBe(true);expect(foregroundActivity().requests).toBe(0);
  });
});
