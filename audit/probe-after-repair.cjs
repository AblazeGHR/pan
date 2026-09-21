// Read-only product-source probes: real Zustand, API/browser boundaries mocked.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const dependencies = process.env.AUDIT_DEPS || 'D:/project/pan-worktrees/t062-12-frontend-consistency-luna-max-20260920/packages/web/node_modules';
const ts = require(path.join(dependencies, 'typescript'));
const zustand = require(path.join(dependencies, 'zustand'));
const root = path.resolve(__dirname, '..');
function compile(relative, mocks, extra = '') {
  const exports = {};
  const source = fs.readFileSync(path.join(root, relative), 'utf8') + extra;
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  vm.runInNewContext(code, {exports, require: n => {
    if (n in mocks) return mocks[n];
    throw new Error('Unmocked dependency ' + n);
  }, console, setTimeout, clearTimeout}, {filename: relative});
  return exports;
}
const identity = compile('packages/web/src/utils/messageIdentity.ts', {});
let requests = [];
const api = {fetchSessionHistory: () => new Promise(resolve => requests.push(resolve)), fetchSessions: async () => []};
const ordering = compile('packages/web/src/stores/messageOrdering.ts', {});
const mod = compile('packages/web/src/stores/sessionStore.ts', {
  zustand,
  '@/services/api': api,
  '@/demo/mockBackend': {isMockMode: () => false},
  '@/stores/uiStore': {useUIStore: {getState: () => ({sortBy:'name'})}},
  '@/utils/messageIdentity': identity,
  '@/stores/messageOrdering': ordering,
});
const store = mod.useSessionStore;
const baseline = {...store.getState()};
const message = (role, content, id) => ({role,content,...(id ? {messageId:id,nativeItemId:id} : {})});
const meta = {serverEpoch:'server',workerId:'worker',generation:0,taskSeq:1,taskId:'task-1'};
const snapshots = [];
function reset(history = [], start = 0, total = history.length) {
  requests = [];
  store.setState({...baseline, sessions:[{id:'A',history,historyStart:start,historyTotal:total,historyEpoch:'hist',historyRevision:10}], currentSessionId:'A',currentMessages:history,historyWindowStarts:{A:start},liveStreamBuffers:{},terminalWatermarks:{},_selectionSeq:{},_historyPageSeq:{},_historyRefreshSeq:{}});
}
function view() {return store.getState().currentMessages.map(m => ({role:m.role,text:m.content,id:m.messageId || m.nativeItemId || null}));}
function record(name,expected,additional = {}) {
  const actual = view();
  let passes;
  try {assert.deepEqual(JSON.parse(JSON.stringify(actual.map(m=>m.text))),expected);passes=true;} catch {passes=false;}
  snapshots.push({name,expected,actual,passes,...additional});
}
async function page(history,start,total,revision=11,epoch='hist') {
  const p = store.getState().refreshCurrentSessionHistory();
  requests.shift()({history,start,total,hasMore:start>0,historyRevision:revision,historyEpoch:epoch});
  await p;
}
(async () => {
  reset([message('user','question','u')]);
  store.getState().applyLiveStream('A',[message('assistant','analysis','a'),message('tool','tool','t'),message('assistant','final','f')],meta);
  record('ordered-stream-before-result',['question','analysis','tool','final']);
  store.getState().reconcileWorkerResult('A',{result:'final',status:'done'},meta);
  record('ordered-stream-after-result',['question','analysis','tool','final']);
  store.getState().addMessage({role:'system',content:'DONE-1',nativeItemId:'done-1'});
  store.getState().appendDeliveredMessages('A',[{role:'user',content:'question2',queueItemIds:['q2']}]);
  const secondMeta={...meta,taskSeq:2,taskId:'task-2'};
  store.getState().applyLiveStream('A',[message('assistant','second analysis','a2'),message('assistant','second final','f2')],secondMeta);
  store.getState().reconcileWorkerResult('A',{result:'second final'},secondMeta);
  record('second-result-loses-earlier-turn-blocks',['question','analysis','tool','final','DONE-1','question2','second analysis','second final']);

  reset([message('assistant','same reply')]);
  store.getState().appendDeliveredMessages('A',[{role:'user',content:'new question',queueItemIds:['new-q']}]);
  store.getState().applyLiveStream('A',[message('assistant','same reply')],meta);
  record('different-task-idless-same-text-swallowed',['same reply','new question','same reply']);

  reset([message('user','question','u')]);
  store.getState().applyLiveStream('A',[message('assistant','live','a')],meta);
  store.setState(s=>({currentMessages:[message('assistant','old-history','old'),...s.currentMessages]}));
  store.getState().applyLiveStream('A',[message('assistant','live updated','a')],meta);
  record('cached-index-after-prepend',['old-history','question','live updated']);

  const full = Array.from({length:6},(_,i)=>message(i%2?'assistant':'user','m'+i,'m'+i));
  reset(full,0,6);
  await page(full.slice(4),4,6);
  store.setState({historyLoading:false,historyLoadEnd:4});
  const older = store.getState().loadOlderMessages();
  requests.shift()({history:full.slice(2,4),start:2,total:6,hasMore:true,historyRevision:11,historyEpoch:'hist'});
  await older;
  record('tail-refresh-then-load-overlapping-old-page',full.map(m=>m.content),{windowStart:store.getState().historyWindowStarts.A});

  reset([message('assistant','revision10','same')]);
  await page([message('assistant','revision9','same')],0,1,9);
  record('history-revision-regression',['revision10'],{revision:store.getState().sessions[0].historyRevision});

  reset([message('user','old user','old-u'),message('assistant','old answer','old-a')]);
  await page([message('user','replacement','new-u')],0,1,20,'replacement-epoch');
  record('history-epoch-replacement-removes-old-tail',['replacement']);

  reset([message('user','question','u')]);
  const idless=[message('assistant','interim'),message('tool','tool'),message('assistant','final')];
  store.getState().applyLiveStream('A',idless,meta);
  store.getState().reconcileWorkerResult('A',{result:'final'},meta);
  record('idless-provider-result',['question','interim','tool','final']);

  const dir=path.join(root,'audit','evidence');fs.mkdirSync(dir,{recursive:true});
  fs.writeFileSync(path.join(dir,'probe-after-repair.json'),JSON.stringify({baseline:'591367a65f88e9d5270e8e99920ef5448d1d68c9',runtime:'real Zustand; transpiled unmodified product TS; mocked API/UI',snapshots},null,2));
  console.log(JSON.stringify(snapshots,null,2));
})().catch(e=>{console.error(e);process.exitCode=1;});
