"""Actual practical-data copy/loader gate. Never start Pan or a provider.

Only aggregate counts, hashes and error types leave the owner-only copy.
Source opens are binary read-only; SQLite copies use mode=ro backup.
"""
from __future__ import annotations
import argparse
import ast
import asyncio
import collections
import copy
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
SOURCE=Path('D:/project/Pan').resolve()
PRIVATE=Path((OUT/'private-root.txt').read_text(encoding='utf-8').strip()).resolve()
DATA=PRIVATE/'compatibility-final/data'
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT))
PHASE='snapshot'
COUNTS=collections.Counter()


def inside(p,root):
    return p==root or root in p.parents


def guard(event,args):
    if event in {'subprocess.Popen','os.system','os.spawn','os.posix_spawn','socket.connect','socket.bind','socket.getaddrinfo'}:
        COUNTS['blockedEffect']+=1
        raise PermissionError('Data gate forbids process/network effects')
    if event=='sqlite3.connect':
        value=str(args[0])
        if str(SOURCE).lower().replace('\\','/') in value.lower().replace('\\','/'):
            if PHASE!='snapshot' or 'mode=ro' not in value:
                raise PermissionError('Source SQLite must be read-only backup only')
        return
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        p=Path(os.fsdecode(args[0])).resolve()
        mode=args[1] or ''
        flags=args[2] or 0
        writing=any(c in mode for c in 'wax+') or bool(flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND))
        if inside(p,SOURCE/'data') or p==SOURCE/'config.json':
            if writing or PHASE=='loaders':
                COUNTS['blockedSourceAccess']+=1
                raise PermissionError('Source data never available to candidate loaders')
            COUNTS['sourceReadOpens']+=1
        if writing and not (inside(p,PRIVATE) or inside(p,OUT)):
            COUNTS['blockedOtherWrite']+=1
            raise PermissionError('Data gate permits writes only to private copy/audit')
    if event in {'os.mkdir','os.remove','os.rmdir','os.rename'}:
        for raw in args[:2] if event=='os.rename' else args[:1]:
            if not isinstance(raw,(str,bytes,os.PathLike)):continue
            p=Path(os.fsdecode(raw)).resolve()
            if not (inside(p,PRIVATE) or inside(p,OUT)):
                COUNTS['blockedOtherWrite']+=1
                raise PermissionError('No external filesystem mutation')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode('utf-8')).hexdigest()


def file_hash(path,limit=None):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while limit is None or limit>0:
            chunk=f.read(1024*1024 if limit is None else min(limit,1024*1024))
            if not chunk:break
            h.update(chunk)
            if limit is not None:limit-=len(chunk)
    return h.hexdigest()


def copy_file(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    for attempt in range(3):
        begin=time.time();st=source.stat()
        with source.open('rb') as f:raw=f.read(st.st_size)
        if source.suffix=='.jsonl' and raw and not raw.endswith(b'\n'):
            raw=raw[:raw.rfind(b'\n')+1]
        target.write_bytes(raw)
        h=hashlib.sha256(raw).hexdigest()
        post=source.stat()
        same_prefix=file_hash(source,len(raw))==h
        complete=(source.suffix=='.jsonl' or (st.st_size==post.st_size and st.st_mtime_ns==post.st_mtime_ns))
        if same_prefix and complete:
            return {'relative':str(source.relative_to(SOURCE/'data')),'key':digest(str(source.relative_to(SOURCE/'data'))),
                'sha256':h,'copiedBytes':len(raw),'sourceBytesBefore':st.st_size,'sourceBytesAfter':post.st_size,
                'sourceMtimeBefore':st.st_mtime_ns,'sourceMtimeAfter':post.st_mtime_ns,
                'prefixVerified':True,'appendOrChangeDuringCopy':st.st_size!=post.st_size or st.st_mtime_ns!=post.st_mtime_ns,
                'startedAt':begin,'endedAt':time.time(),'attempts':attempt+1}
    raise RuntimeError('unstable_copy')


def snapshot():
    DATA.mkdir(parents=True,exist_ok=True)
    stores=['sessions','background_jobs','scheduler','actions','qq_inbox','qq_history','agent_inbox','user_inbox','automation']
    inventory={};manifest=[];sqlite_count=0
    for name in stores:
        p=SOURCE/'data'/name
        files=[q for q in p.rglob('*') if q.is_file()] if p.exists() else []
        selected=[q for q in files if q.suffix in {'.json','.jsonl','.sqlite','.sqlite3','.db'}]
        # Automation may contain unrelated user scripts. Only product state
        # filenames are inputs, never credentials, scripts, logs or locks.
        if name=='automation':selected=[q for q in selected if q.parent==p and q.name in {'registry.json','events.sqlite3','quota-policy.json'}]
        inventory[name]={'directoryExists':p.exists(),'allFiles':len(files),'selectedFiles':len(selected),
            'types':dict(collections.Counter(q.suffix for q in selected)), 'bytes':sum(q.stat().st_size for q in selected)}
        for q in selected:
            target=DATA/q.relative_to(SOURCE/'data')
            if q.suffix in {'.sqlite','.sqlite3','.db'}:
                target.parent.mkdir(parents=True,exist_ok=True)
                start=time.monotonic()
                with sqlite3.connect(q.as_uri()+'?mode=ro',uri=True,timeout=5) as src,sqlite3.connect(target) as dest:
                    src.execute('PRAGMA query_only=ON')
                    def progress(*args):
                        if time.monotonic()-start>20:raise TimeoutError('backup_timeout')
                    src.backup(dest,pages=128,progress=progress,sleep=.01)
                sqlite_count+=1
                manifest.append({'relative':str(q.relative_to(SOURCE/'data')),'key':digest(str(q.relative_to(SOURCE/'data'))),
                    'method':'read_only_sqlite_backup','copySha256':file_hash(target)})
            else:manifest.append(copy_file(q,target))
    cfg=json.loads((SOURCE/'config.json').read_text(encoding='utf-8'))
    schema={'topLevelKeys':sorted(cfg),'storagePointers':{k:cfg[k] for k in ('data_dir','dataDir','session_dir','sessionsDir','background_jobs_dir','scheduler_dir','actions_dir') if k in cfg},
        'pluginManifestCount':len(cfg.get('plugin_manifests') or []),'configSha256':file_hash(SOURCE/'config.json'),
        'runtimeEnvironmentRead':False,'layoutResolution':'checkout-default paths; no explicit storage pointers in config; runtime environment not collected'}
    (PRIVATE/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    public=[{k:v for k,v in row.items() if k!='relative'} for row in manifest]
    (OUT/'snapshot.json').write_text(json.dumps({'stores':inventory,'config':schema,'files':public,
        'sqliteSourceBackupCount':sqlite_count,'globalTransactionSnapshot':False,'sampling':'per-file verified bytes/prefix; metadata/history pairs sampled independently',
        'sampleStartedAt':min(r.get('startedAt',time.time()) for r in manifest),'sampleEndedAt':time.time()},indent=2),encoding='utf-8')


RESULTS=[]
def case(name,fn):
    start=time.monotonic()
    try:details=fn();RESULTS.append({'name':name,'status':'passed','details':details,'seconds':time.monotonic()-start})
    except Exception as exc:
        RESULTS.append({'name':name,'status':'failed','errorType':type(exc).__name__,'seconds':time.monotonic()-start})
        # Deliberately do not render exception messages, locals or payloads.


def loaders():
    global PHASE
    for key in list(os.environ):
        if key.startswith('PAN_'):os.environ.pop(key)
    # No listener is needed for this gate. Keep a deterministic private port
    # value only; the audit hook rejects every socket operation.
    port=50000
    assert port not in (8767,8768)
    os.environ.update({'PAN_BACKGROUND_JOBS_DIR':str(DATA/'background_jobs'),
        'PAN_SESSION_ERROR_LOG_PATH':str(PRIVATE/'loader-errors.jsonl'),'PAN_PORT':str(port)})
    PHASE='loaders'
    logging.disable(logging.CRITICAL)
    from packages.core import session,config,background_jobs as jobs,worker,workspace
    from packages.scheduler import store as scheduler
    from packages.actions.store import Store
    from packages.jobs import unified,system_actions
    from packages.jobs.action_templates import Templates
    from packages.core.agent_inbox import Inbox
    from packages.core.inbox_service import migrate_qq
    from packages.interactions.store import InteractionStore
    from packages.automation.store import Ledger
    from packages.automation.event_store import EventStore
    session.SESSION_DIR=DATA/'sessions';session._cache.clear();session._all_loaded=False
    workspace.WORKSPACE_DIR=PRIVATE/'empty-workspaces'
    config.CONFIG_FILE=PRIVATE/'safe-config.json'
    jobs.DEFAULT_ROOT=DATA/'background_jobs';scheduler.DEFAULT_ROOT=DATA/'background_jobs';scheduler.LEGACY_TASKS_ROOT=DATA/'scheduler'
    config.CONFIG_FILE.write_text(json.dumps({'port':port,'scheduler':{'enabled':False},'memory':{'enabled':False},
        'qq':{'enabled':False},'plugin_manifests':[],'independent_jobs':{'enabled':False},
        'cbc':{'cli':str(ROOT/'tests/support/fake_stream_cli.py')}}),encoding='utf-8')
    worker._shutdown_started=True
    # No Service.start / Engine.start / Runtime.start / scheduler.start / recovery
    # controller is called. The audit hook rejects even accidental effects.
    def sessions():
        paths=sorted((DATA/'sessions').glob('*.json'))
        qcount=lcount=hist_count=unknownq=changed=0
        queue_states=collections.Counter();migration_count=0
        for path in paths:
            raw=json.loads(path.read_text(encoding='utf-8'));sid=raw['id']
            before=file_hash(path);s=session.get(sid,load_history=False)
            assert s is not None and file_hash(path)==before
            for key in ('queue_pending','queue_delivery_ledger','queue_idempotency_index','manual_report_receipts','execution_operations'):
                if key in raw:assert digest(getattr(s,key))==digest(raw[key])
            qcount+=len(raw.get('queue_pending') or []);lcount+=len(raw.get('queue_delivery_ledger') or {})
            unknownq+=sum('unknown' in str(r.get('deliveryState','')) or bool(r.get('queueSteerPending')) for r in raw.get('queue_pending',[]) if isinstance(r,dict))
            queue_states.update(str(r.get('deliveryState','legacy')) for r in raw.get('queue_pending',[]) if isinstance(r,dict))
            s=session.get(sid,load_history=True)
            assert s is not None and file_hash(path)==before
            # Remap only the in-memory copy for safe save validation.
            s.workdir=str(PRIVATE/'empty-workspaces'/sid)
            (PRIVATE/'empty-workspaces'/sid).mkdir(parents=True,exist_ok=True)
            hist_count+=len(s.history)
            original_queue=copy.deepcopy(s.queue_pending)
            original_ledger=copy.deepcopy(s.queue_delivery_ledger)
            migration_count+=bool(worker._migrate_queue_delivery_state(s,restore_ledger=True))
            assert worker._migrate_queue_delivery_state(s,restore_ledger=True) is False
            for qid,record in original_ledger.items():
                assert qid in s.queue_delivery_ledger
                if record.get('deliveryState') in worker._DELIVERY_UNCERTAIN_STATES:
                    assert s.queue_delivery_ledger[qid]['deliveryState']==record['deliveryState']
            by_id={worker._queue_item_id(r):r for r in s.queue_pending if isinstance(r,dict)}
            for original in original_queue:
                if not isinstance(original,dict):continue
                qid=worker._queue_item_id(original)
                receipt=original_ledger.get(qid,{})
                if original.get('deliveryState')=='sent_to_cli' or receipt.get('deliveryState')=='sent_to_cli':continue
                assert qid in by_id
                if original.get('deliveryState') in worker._DELIVERY_UNCERTAIN_STATES:
                    assert by_id[qid]['deliveryState']==original['deliveryState']
            essential={k:copy.deepcopy(getattr(s,k)) for k in ('queue_pending','queue_delivery_ledger','queue_idempotency_index','manual_report_receipts','execution_operations')}
            hpath=session._history_path(sid);hh=file_hash(hpath) if hpath.exists() else None
            session.save(s)
            first=json.loads(path.read_text(encoding='utf-8'))
            for k,v in essential.items():assert digest(first[k])==digest(v)
            canonical=set(first)
            consumed={'system_prompt','recovery_requested','recoveryRequested','lastLegalWorkerState','activeTaskId','historyEpoch','historyRevision','queueIdempotencyIndex','idempotency_index','restrict_to_managed','can_claim_unmanaged','auto_claim_created','cbc_session_id','always_thinking_enabled','effort','max_thinking_tokens'}
            for k in raw:
                if k not in canonical and k not in consumed:raise AssertionError('lost_top_level_field')
            if hh:assert file_hash(hpath)==hh
            session._cache.pop(sid,None);loaded=session.get(sid,load_history=False)
            loaded.workdir=str(PRIVATE/'empty-workspaces'/sid)
            session.save(loaded)
            second=json.loads(path.read_text(encoding='utf-8'))
            for k,v in essential.items():assert digest(second[k])==digest(v)
            assert len(second.get('history',[]))==len(first.get('history',[]))
            changed+=file_hash(path)!=before
            session._cache.clear()
        return {'sessionCount':len(paths),'historyRecordsRead':hist_count,'queuePendingCount':qcount,'deliveryLedgerCount':lcount,
            'unknownOrSteerPendingRows':unknownq,'queueStates':dict(queue_states),'metadataFilesUpgradedOnCopy':changed,
            'queuesAndLedgersExactAcrossTwoSaves':True,'historyBytesUnchanged':True,
            'actualQueueMigrationSessionsChanged':migration_count,'secondMigrationStable':True}
    case('sessions_queue_load_upgrade_reload',sessions)
    def unknown_queue():
        # The real sample has no unknown pending rows. Exercise this required
        # safety boundary separately with synthetic content and product logic.
        s=session.Session(id='ses_gate_unknown',name='Synthetic gate probe')
        s.queue_pending=[{'type':'task','id':'q_gate_unknown','queueItemId':'q_gate_unknown',
            'kind':'task','text':'synthetic gate content','source':'user',
            'createdAt':1,'deliveryState':worker._DELIVERY_UNKNOWN}]
        worker._migrate_queue_delivery_state(s)
        result={'synthetic':True,'before':worker._DELIVERY_UNKNOWN,'after':s.queue_pending[0]['deliveryState'],
            'originalQueueIdPreserved':s.queue_pending[0]['queueItemId']=='q_gate_unknown',
            'dispatchInvoked':False,'businessSideEffectInvoked':False}
        (OUT/'unknown-queue-probe.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        assert result['after']==worker._DELIVERY_UNKNOWN, 'unknown_queue_became_retryable'
        return result
    case('legacy_unknown_queue_remains_nonretryable',unknown_queue)
    def job_check():
        paths=sorted((DATA/'background_jobs/jobs').glob('job_*.json'))
        before={p:file_hash(p) for p in paths}
        raw=[json.loads(p.read_text(encoding='utf-8')) for p in paths]
        loaded=jobs.list_jobs(DATA/'background_jobs');assert len(loaded)==len(raw)
        byid={r['jobId']:r for r in loaded};kinds=collections.Counter();states=collections.Counter();unknownfields=0
        for r in raw:
            j=byid[r['jobId']];kinds[j['kind']]+=1;states[str(j.get('status'))]+=1
            for k,v in r.items():assert digest(j[k])==digest(v)
            v=jobs.job_public_view(j);assert 'attemptSnapshot' not in v
            projected=unified.project(j)
            if not (isinstance(j.get('config'),dict) and j.get('action')):
                assert projected['config']['enabled'] is False
                assert projected['config']['recovery']['enabled'] is False
                unknownfields+=len(projected['compatibility']['unknownFields'])
        assert jobs.list_jobs(DATA/'background_jobs')==loaded
        assert all(file_hash(p)==h for p,h in before.items())
        migrated=scheduler.migrate_legacy_tasks();again=scheduler.migrate_legacy_tasks();assert again==0
        return {'jobs':len(loaded),'kinds':dict(kinds),'statuses':dict(states),'unknownFieldsKeptInProjection':unknownfields,
            'legacySchedulerMigrated':migrated,'secondMigration':again,'registryBytesUnchangedByRead':True,'scheduledTaskViews':len(scheduler.list_tasks())}
    case('jobs_and_scheduler_no_enable_no_dispatch',job_check)
    def actions():
        store=Store(DATA/'actions');old=store.read();oldhash=file_hash(store.path)
        assert store.read()==old and file_hash(store.path)==oldhash
        templates=Templates(store).list()
        system_actions.install(store);first=store.read();system_actions.install(store);second=store.read()
        assert first==second
        for key,val in old.items():
            if key in ('revision','actions','actionRevisions'):continue
            assert second[key]==val
        for aid,a in old['actions'].items():assert second['actions'][aid]==a
        return {'originalActions':len(old['actions']),'originalGroups':len(old['groups']),'templates':len(templates),
            'productSystemActionsAdded':len(second['actions'])-len(old['actions']),'repeatInstallStable':True,'originalDefinitionsPreserved':True}
    case('action_group_template_initialize',actions)
    class NoQueue:
        async def put(self,*args,**kw):raise AssertionError('no_delivery_allowed')
        async def lookup(self,*args,**kw):raise AssertionError('no_delivery_allowed')
    def inbox():
        box=Inbox(DATA/'agent_inbox/inbox.sqlite',NoQueue())
        legacy=DATA/'qq_inbox';before={p:file_hash(p) for p in legacy.rglob('*.json')}
        expected=0
        for p in before:
            value=json.loads(p.read_text(encoding='utf-8'))
            expected += len(value) if isinstance(value,(list,dict)) else 0
        migrate_qq(box,legacy)
        with box.db() as db:
            count=db.execute('SELECT count(*) FROM messages').fetchone()[0]
            snapshot=[tuple(r) for r in db.execute('SELECT * FROM messages ORDER BY seq')]
        assert count==expected
        box=Inbox(box.path,NoQueue());migrate_qq(box,legacy)
        with box.db() as db:assert [tuple(r) for r in db.execute('SELECT * FROM messages ORDER BY seq')]==snapshot
        assert all(file_hash(p)==h for p,h in before.items())
        return {'legacyFiles':len(before),'legacyRecords':expected,'newAgentInboxMessages':count,'lockers':len(box.list()),
            'repeatImportNoDuplicate':True,'consumptionNotInvoked':True,'sourceCopyJsonBytesUnchanged':True}
    case('legacy_qq_agent_inbox_copy_migration',inbox)
    def qq_history():
        # Execute the exact pure read functions from product source without
        # importing the optional NoneBot host (which registers lifecycle hooks).
        source=ROOT/'packages/qq/plugin.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        selected=[node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))
            and node.name in {'_history_path','_load_history'}]
        assert len(selected)==2
        namespace={'Path':Path,'json':json,'_HISTORY_DIR':DATA/'qq_history'}
        exec(compile(ast.Module(body=selected,type_ignores=[]),str(source),'exec'),namespace)
        count=records=0
        for path in (DATA/'qq_history').rglob('*.json'):
            raw=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(raw,list):continue
            target=path.stem;bot=path.parent.name if path.parent!=DATA/'qq_history' else None
            h=file_hash(path)
            # This product coroutine contains no awaits. Drive it directly so
            # Windows asyncio cannot create an internal self-pipe socket.
            coroutine=namespace['_load_history'](target,bot)
            try:
                coroutine.send(None)
                raise AssertionError('history_loader_unexpected_suspend')
            except StopIteration as completed:
                result=completed.value
            finally:
                coroutine.close()
            assert result==raw and file_hash(path)==h
            count+=1;records+=len(raw)
        return {'historyFiles':count,'historyRecords':records,'readOnlyLoader':True,
            'executionBoundary':'exact source AST functions; optional NoneBot host not imported'}
    case('legacy_qq_history_loader',qq_history)
    def new_stores():
        user=InteractionStore(DATA/'user_inbox/cards.sqlite')
        with user.db() as db:
            assert db.execute('SELECT count(*) FROM cards').fetchone()[0]==0
        user=InteractionStore(user.path)
        with user.db() as db:assert db.execute('SELECT count(*) FROM cards').fetchone()[0]==0
        events=EventStore(DATA/'automation');events.initialize();events.initialize()
        assert events.page()['events']==[]
        ledger=Ledger(DATA/'automation');assert ledger.read()['runs']=={} and ledger.read()['triggers']=={}
        return {'sourceUserLockerAbsent':True,'sourceEventStoreAbsent':True,'sourceTriggerLedgerAbsent':True,
            'emptyFirstInitializeAndRepeatSafe':True,'noSyntheticLegacyRowsRepresentedAsReal':True}
    case('absent_new_stores_first_init',new_stores)


def finish():
    global PHASE
    PHASE='postcheck'
    manifest=json.loads((PRIVATE/'manifest.json').read_text(encoding='utf-8'))
    same=changed=missing=0
    for row in manifest:
        if 'sha256' not in row:continue
        p=SOURCE/'data'/row['relative']
        if not p.exists():missing+=1;continue
        equal=file_hash(p,row['copiedBytes'])==row['sha256']
        current=p.stat()
        if equal and current.st_size==row['sourceBytesAfter'] and current.st_mtime_ns==row['sourceMtimeAfter']:same+=1
        else:changed+=1
    result={'candidateSha':subprocess_sha,'privateCopyRetained':str(PRIVATE),'results':RESULTS,'guardCounters':dict(COUNTS),
        'sourcePostcheck':{'unchangedFiles':same,'activeChangedFiles':changed,'missingNow':missing,
            'taskSourceWrites':0,'proof':'source opened rb only; all candidate source access denied; SQLite source mode=ro only; no source loader invoked'},
        'globalSnapshot':False,'fullServiceStarted':False,'networkOrProvidersInvoked':False}
    (OUT/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    suite=ET.Element('testsuite',name='practical_actual_copy_gate',tests=str(len(RESULTS)),failures=str(sum(r['status']=='failed' for r in RESULTS)))
    for r in RESULTS:
        el=ET.SubElement(suite,'testcase',name=r['name'],time=str(round(r['seconds'],3)))
        if r['status']=='failed':ET.SubElement(el,'failure',type=r['errorType'],message='Payload omitted; see safe count evidence')
    ET.ElementTree(suite).write(OUT/'junit.xml',encoding='utf-8',xml_declaration=True)
    print(json.dumps({'passed':sum(r['status']=='passed' for r in RESULTS),'failed':sum(r['status']=='failed' for r in RESULTS),
        'sourceActiveChanges':changed,'guardCounters':dict(COUNTS)}))
    return int(any(r['status']=='failed' for r in RESULTS))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--reuse-copy',action='store_true');args=parser.parse_args()
    subprocess_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,encoding='utf-8').strip()
    sys.addaudithook(guard)
    if not args.reuse_copy:snapshot()
    try:
        loaders()
    except Exception as exc:RESULTS.append({'name':'loader_setup','status':'failed','errorType':type(exc).__name__,'seconds':0})
    raise SystemExit(finish())
