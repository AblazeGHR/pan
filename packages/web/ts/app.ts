// ── Types ──

interface Message {
  role: string;
  content: string;
}

interface Session {
  id: string;
  name: string;
  adapter?: string;
  cliSessionId?: string | null;
  model?: string | null;
  permissionMode?: string | null;
  alwaysThinkingEnabled: boolean;
  effort: string;
  maxThinkingTokens?: number;
  workdir?: string;
  workerStatus?: string | null;
  workerId?: string | null;
  mcpEnabled?: boolean;
  mcpLocked?: boolean | null;
  history: Message[];
  historyTruncated?: boolean;
  historyTotal?: number;
  lastResult?: Record<string, unknown> | null;
  totalUsage?: Record<string, number> | null;
  managed?: string[];
  managedBy?: string | null;
  reportSubscriptions?: string[];
  qqSubscriptions?: string[];
}

interface WorkerEventContent {
  type: string;
  text?: string;
  thinking?: string;
  name?: string;
  input?: Record<string, unknown>;
}

interface WorkerEvent {
  type: string;
  subtype?: string;
  message?: {
    content?: WorkerEventContent[];
  };
  session_id?: string;
  model?: string;
  is_error?: boolean;
  result?: string;
  cliSessionId?: string;
}

interface StreamEvent {
  type: string;
  sessionId?: string;
  workerId?: string;
  event?: WorkerEvent;
  message?: string;
  status?: string;
  name?: string;
  cliSessionId?: string;
}

interface ApiSessionsResponse {
  sessions: Session[];
  error?: string;
}

interface ApiModelsResponse {
  models: string[];
  default: string;
}

interface ApiGenericResponse {
  error?: string;
  workerId?: string;
  sessionId?: string;
  status?: string;
  cliSessionId?: string;
  takeoverCommand?: string;
}

/** Error detail for endpoints that return {ok:false, error:{code,message}}. */
interface ApiErrorDetail {
  code?: string | number;
  message?: string;
}

/** Response shared by claim/unclaim/qq-subscribe style endpoints (error may be
 *  either a plain string or the {code,message} object shape). */
interface ApiOpResponse {
  ok?: boolean;
  error?: string | ApiErrorDetail;
}

/** QQ contact from GET /api/qq/contacts (chatType: 1=私聊, 2=群). */
interface QqContact {
  peerName: string;
  peerUin: string;
  chatType: number;
}

interface ApiQqContactsResponse {
  ok?: boolean;
  contacts?: QqContact[];
  error?: string | ApiErrorDetail;
}

interface SessionTemplate {
  name: string;
  adapter?: string;
  model?: string;
  mcpServers?: string[];
  sourceManifest?: string;
  sourceManifestLabel?: string;
  system_prompt_preview?: string;
}

interface ApiSessionTemplatesResponse {
  sessionTemplates?: SessionTemplate[];
  error?: string;
}

interface AdapterConfig {
  models: string[];
  defaultModel: string;
  effortValues: string[];
  permissionModes: {value: string; label: string}[];
  defaultPermissionMode: string;
  supportedSettings: string[];
}

interface ApiConfigResponse {
  adapter?: string;
  models: string[];
  defaultModel: string;
  effortValues: string[];
  permissionModes: {value: string; label: string}[];
  defaultPermissionMode?: string;
  supportedSettings?: string[];
}

interface SyncedSettings {
  model: string;
  permissionMode: string;
  alwaysThinkingEnabled: boolean;
  effort: string;
}

/** 客户端发送队列项（localStorage 按 sessionId 持久化）。 */
interface QueuedMessage {
  id: string;        // 唯一标识（重排/编辑/删除的 key）
  text: string;      // 原文（渲染时单行截断，存全文）
  createdAt: number; // 入队时间戳
  status: 'pending'; // 首版恒 pending，预留扩展
}

// ── State ──

let availableAdapters: string[] = [];
const adapterConfigs: Map<string, AdapterConfig> = new Map();
let currentAdapter: string = 'cbc';
let _adapterConfigReady: boolean = false;

// Friendly display labels for known adapters in selects (fallback: raw name).
const ADAPTER_LABELS: Record<string, string> = {
  cbc: 'cbc (CodeBuddy CLI)',
  kimi: 'kimi (Kimi CLI)',
  opencode: 'opencode',
};
function adapterLabel(name: string): string {
  return ADAPTER_LABELS[name] || name;
}

// Cached config getters for the currently selected adapter
function allModels(): string[] { return adapterConfigs.get(currentAdapter)?.models || []; }
function defaultModel(): string { return adapterConfigs.get(currentAdapter)?.defaultModel || 'deepseek-v4-flash'; }
function effortValues(): string[] { return adapterConfigs.get(currentAdapter)?.effortValues || []; }
function permissionModes(): {value: string; label: string}[] { return adapterConfigs.get(currentAdapter)?.permissionModes || []; }
function defaultPermissionMode(): string { return adapterConfigs.get(currentAdapter)?.defaultPermissionMode || ''; }
function supportedSettings(): string[] { return adapterConfigs.get(currentAdapter)?.supportedSettings || ['model', 'permissionMode', 'thinking', 'effort']; }
function supportsSetting(name: string): boolean { return supportedSettings().indexOf(name) >= 0; }

let currentSessionId: string | null = null;
let currentWorkerId: string | null = null;
let modelData: Session[] = [];
let _multiSelectMode = false;
let _selectedIds: Set<string> = new Set();
let lastSyncedSettings: SyncedSettings | null = null;
let bubbleViewEnabled: boolean = true;
let currentHistory: Message[] = [];
let toolGroupOpen: boolean = false;
let _currentToolGroupStart: number = -1;
let _rendering: boolean = false;
let _historyLoading: boolean = false;
let _historyLoadEnd: number = 0;
/** True when loadOlderMessages was triggered by session switch (initial load)
 *  rather than user scroll-up; signals the callback to scroll to bottom. */
let _loadOlderToBottom: boolean = false;
const _inputDrafts: Map<string, string> = new Map();
/** 发送队列：localStorage key 前缀 + 每 session 上限 */
const _QUEUE_KEY_PREFIX = 'pan.sendQueue.';
const _QUEUE_MAX = 50;
/** 批量拼接发送开关的 localStorage key 前缀（per-session 布尔） */
const _BATCH_KEY_PREFIX = 'pan.sendQueue.batch.';
/** 批量拼接发送在单飞锁 `_queueSendingId` 中使用的哨兵值（真实队列项 id 不可能等于它） */
const _BATCH_SENDING_ID = '__batch__';
/** 内存镜像：按 sessionId 隔离，随 storage 读写同步 */
const _queueCache: Map<string, QueuedMessage[]> = new Map();
/** 当前正在发送的队列项 id（防 idle 事件重复触发导致同一条被发两次）；
 *  批量拼接发送时置为 `_BATCH_SENDING_ID`，拼接消息也算 1 条发送中。 */
let _queueSendingId: string | null = null;
/** 编辑中出队消息的待恢复记录（localStorage 按 session 持久化，刷新/切换 session 后恢复）。
 *  编辑中的消息从队列出队后写入；保存/取消时清除。 */
const _QUEUE_EDIT_KEY_PREFIX = 'pan.sendQueue.edit.';
interface QueuedEditPending {
  id: string;          // 出队消息 id（恢复后仍是原 id，保证不重复）
  sessionId: string;   // 所属 session
  index: number;       // 原位置（保存/取消时插回）
  originalText: string; // 原文（取消 / 刷新恢复用）
  draftText: string;   // 编辑框当前内容（仅内存，重绘时保留）
  createdAt: number;   // 原入队时间（插回时保留，恢复后顺序一致）
}
/** 当前编辑中的出队消息（全局同一时刻至多一条）。
 *  编辑期间该消息不在队列里，flushQueue()/getQueue() 都看不到，不可能被自动发出。 */
let _editingPending: QueuedEditPending | null = null;
/** Per-session set of unread thinking/tool content hashes */
const _sessionUnread: Map<string, Set<string>> = new Map();

// ── Manage / Postbox modal state ──
let _manageSessionId = '';
let _manageFilter = '';
let _manageShowAll = false;
let _postboxSessionId = '';
let _postboxFilter = '';
let _postboxShowAll = false;
let _postboxContacts: QqContact[] | null = null;
let _postboxError = '';

function _getUnread(): Set<string> {
  if (!currentSessionId) return new Set();
  let s = _sessionUnread.get(currentSessionId);
  if (!s) { s = new Set(); _sessionUnread.set(currentSessionId, s); }
  return s;
}

// ── Render guards ──
// Tracks the last history tail we rendered for the current session, so WS-driven
// refreshSessions() can skip full re-renders when nothing has changed.
let _renderedFor: { sessionId: string | null; tailRole: string; tailContent: string } = {
  sessionId: null,
  tailRole: '',
  tailContent: '',
};

// Worker state known from WebSocket events (more timely than the HTTP
// /api/sessions snapshot). `refreshSessions()` merges these over the snapshot
// so a stale fetch response never reverts the status indicator / sidebar dot.
// `_wsWorkerTs` records when each event arrived so we only override the
// snapshot when the WS update is newer than the in-flight fetch.
const _wsWorkerState: Map<string, { workerId: string | null; status: string | null }> = new Map();
const _wsWorkerTs: Map<string, number> = new Map();
/** Timestamp when the currently in-flight /api/sessions fetch started. */
let _refreshStartedAt = 0;

/** Tail used for the render guard: last non-system message.
 *  Local-only system messages (e.g. "[DONE] Task completed") never appear in
 *  the server-side history, so comparing them would defeat the guard and
 *  trigger a full rebuild after every task. */
function _tailOf(history: Message[]): { role: string; content: string } {
  const h = history || [];
  for (let i = h.length - 1; i >= 0; i--) {
    if (h[i].role !== 'system') {
      return { role: h[i].role, content: h[i].content || '' };
    }
  }
  return { role: '', content: '' };
}

function _recordRenderedFor(sessionId: string | null, history: Message[]): void {
  const tail = _tailOf(history);
  _renderedFor = {
    sessionId,
    tailRole: tail.role,
    tailContent: tail.content,
  };
}

function _shouldRenderMessages(sessionId: string | null, history: Message[]): boolean {
  const tail = _tailOf(history);
  return !(
    _renderedFor.sessionId === sessionId &&
    _renderedFor.tailRole === tail.role &&
    _renderedFor.tailContent === tail.content
  );
}

/** True when the server-reported history is a prefix of what is already
 *  rendered locally (currentHistory). Local-only trailing messages — the
 *  optimistically added user message that the server hasn't persisted yet
 *  (spawn / resume-replay window), and "[DONE]" system notices — are expected
 *  and must not trigger a full rebuild that would wipe them from the DOM. */
function _isServerHistoryPrefix(serverHistory: Message[]): boolean {
  if (serverHistory.length > currentHistory.length) return false;
  for (let i = 0; i < serverHistory.length; i++) {
    const s = serverHistory[i];
    const c = currentHistory[i];
    if (!c || s.role !== c.role || s.content !== c.content) return false;
  }
  return true;
}

// ── Markdown / LaTeX rendering ──
if (typeof (window as any).marked !== 'undefined') {
  (window as any).marked.setOptions({ breaks: true, gfm: true });
}

// Markdown cache — avoids re-parsing the same content on session switches
const _mdCache: Map<string, string> = new Map();
const _MD_CACHE_MAX = 2000;

function renderMarkdown(text: string): string {
  if (!text) return '';
  const cached = _mdCache.get(text);
  if (cached !== undefined) return cached;

  const mathStore: Array<{ key: string; latex: string; display: boolean }> = [];
  let mathIndex = 0;
  function saveMath(latex: string, display: boolean): string {
    const key = `[[MATH_PLACEHOLDER_${mathIndex++}]]`;
    mathStore.push({ key, latex: latex.trim(), display });
    return key;
  }

  let t = text;
  t = t.replace(/\$\$([\s\S]*?)\$\$/g, (_, latex) => saveMath(latex, true));
  t = t.replace(/\$([^$\n]+?)\$/g, (_, latex) => saveMath(latex, false));

  let html: string;
  if (typeof (window as any).marked !== 'undefined') {
    html = (window as any).marked.parse(t);
  } else {
    html = esc(t).replace(/\n/g, '<br>');
  }

  if (typeof (window as any).katex !== 'undefined') {
    mathStore.forEach(function (item) {
      try {
        const rendered = (window as any).katex.renderToString(item.latex, {
          displayMode: item.display,
          throwOnError: false,
        });
        html = html.split(item.key).join(rendered);
      } catch (e) {
        html = html.split(item.key).join('<code>' + esc(item.latex) + '</code>');
      }
    });
  }

  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  if (typeof (window as any).hljs !== 'undefined') {
    tmp.querySelectorAll('pre code').forEach(function (block) {
      (window as any).hljs.highlightElement(block);
    });
  }
  const result = tmp.innerHTML;
  _mdCache.set(text, result);
  if (_mdCache.size > _MD_CACHE_MAX) {
    // delete oldest entry (Map is insertion-ordered)
    const first = _mdCache.keys().next().value as string;
    _mdCache.delete(first);
  }
  return result;
}

// ── View toggle ──
function toggleView(): void {
  bubbleViewEnabled = !bubbleViewEnabled;
  const btn = document.getElementById('viewToggleBtn')!;
  const msgs = document.getElementById('messages')!;
  if (bubbleViewEnabled) {
    btn.innerHTML = '\uD83D\uDCAC';
    btn.title = 'Switch to TUI view';
    msgs.classList.remove('tui-mode');
  } else {
    btn.innerHTML = '\uD83D\uDDA5\uFE0F';
    btn.title = 'Switch to Bubble view';
    msgs.classList.add('tui-mode');
  }
  renderMessages(currentHistory);
}

// ── WebSocket ──
const wsProtocol = location.protocol === 'https:' ? 'wss://' : 'ws://';
var ws: any;
var _wsUrl: string = wsProtocol + location.host + '/ws';

function connectWs(): void {
  ws = new WebSocket(_wsUrl);
  ws.onopen = refreshSessions;
  ws.onmessage = onWsMessage;
  ws.onclose = function () {
    console.warn('[WS] disconnected, reconnecting in 3s');
    setTimeout(connectWs, 3000);
  };
}
connectWs();

function onWsMessage(e: MessageEvent): void {
  const d: StreamEvent = JSON.parse(e.data);
  switch (d.type) {
    case 'worker.spawned':
    case 'worker.restarted':
    case 'worker.reconfigured':
      _applyWorkerUpdate(d.sessionId, d.workerId, 'idle');
      break;
    case 'worker.destroyed':
    case 'worker.crashed':
      _applyWorkerUpdate(d.sessionId, null, null);
      break;
    case 'worker.stream':
      if (d.sessionId === currentSessionId && d.event) {
        appendEvent(d.event);
      }
      break;
    case 'worker.result':
      if (d.sessionId === currentSessionId) appendResult(d);
      _applyWorkerUpdate(d.sessionId, d.workerId, 'idle');
      break;
    case 'worker.status':
      _applyWorkerUpdate(d.sessionId, d.workerId, d.status?? 'idle');
      break;
    case 'session.renamed':
    case 'session.updated':
      refreshSessions();
      break;
    // session.created / session.deleted: 乐观UI已处理，不触发WS刷新
    case 'error':
      toast(d.message?? 'Unknown error');
      break;
  }
}

/** Apply a worker update from a WebSocket event.
 *  Side effects: syncs currentWorkerId, updateTopBar (incl. mobile dot),
 *  renderSessionList, and triggers a debounced refreshSessions fetch. */
function _applyWorkerUpdate(
  sessionId: string | undefined,
  workerId: string | undefined | null,
  status: string | null
): void {
  if (!sessionId) return;

  _wsWorkerState.set(sessionId, { workerId: workerId ?? null, status });
  _wsWorkerTs.set(sessionId, Date.now());

  for (let i = 0; i < modelData.length; i++) {
    if (modelData[i].id === sessionId) {
      modelData[i].workerId = workerId?? undefined;
      modelData[i].workerStatus = status;
      break;
    }
  }
  if (sessionId === currentSessionId) {
    currentWorkerId = workerId?? null;
    updateTopBar();
  }
  // 队列自动发送：worker 变 idle 且当前 session 队列非空 → 发送队首 1 条
  // （发送后 worker 变 queued/running，不再是 idle，天然防重复；result→idle 再取下一条）
  if (status === 'idle' && sessionId === currentSessionId) {
    flushQueue();
  }
  // In-place sidebar dot update (avoid full list rebuild flicker)
  const list = document.getElementById('sessionList');
  if (list) {
    let found = false;
    list.querySelectorAll('.sess-item').forEach(function (item) {
      const div = item as HTMLElement;
      if (div.dataset.sessionId === sessionId) {
        found = true;
        const dot = div.querySelector('.s-dot');
        if (dot) {
          dot.className = 's-dot ' + (status || 'offline');
        }
      }
    });
    if (!found) renderSessionList();
  }
  scheduleRefreshSessions();
}

// ── Session list ──
let _refreshVersion: number = 0;
let _refreshTimer: ReturnType<typeof setTimeout> | null = null;
let _renderVersion: number = 0;

function scheduleRefreshSessions(): void {
  if (_refreshTimer) clearTimeout(_refreshTimer);
  _refreshTimer = setTimeout(() => {
    _refreshTimer = null;
    refreshSessions();
  }, 300);
}

function refreshSessions(): void {
  _refreshVersion++;
  const version = _refreshVersion;
  const listEl = document.getElementById('sessionList')!;
  // Only show loading spinner on first fetch (empty list); avoid flicker on subsequent updates
  if (listEl.children.length === 0) {
    listEl.innerHTML = '<div class="sidebar-loading">Loading...</div>';
  }
  _refreshStartedAt = Date.now();
  fetch('/api/sessions')
    .then((r: Response) => r.json())
    .then((data: ApiSessionsResponse) => {
      if (version !== _refreshVersion) return;
      modelData = data.sessions || [];
      // A WebSocket worker update that arrived while the fetch was in flight is
      // newer than this snapshot — re-apply it so the status indicator /
      // sidebar dot don't revert to a stale value (spawn, status change,
      // destroy, crash).
      _wsWorkerTs.forEach((ts, sid) => {
        if (ts < _refreshStartedAt) return;
        const wsState = _wsWorkerState.get(sid);
        if (!wsState) return;
        const s = modelData.find((x: Session) => x.id === sid);
        if (!s) return;
        s.workerStatus = wsState.status;
        s.workerId = wsState.workerId ?? undefined;
      });
      // Prune WS state for sessions that no longer exist
      _wsWorkerTs.forEach((_, sid) => {
        if (!modelData.find((x: Session) => x.id === sid)) {
          _wsWorkerTs.delete(sid);
          _wsWorkerState.delete(sid);
        }
      });
      renderSessionList();
      const matched = modelData.find((s: Session) => s.id === currentSessionId);
      if (!matched) {
        currentSessionId = null;
        currentWorkerId = null;
        showEmpty();
      } else {
        const curSid = currentSessionId as string;
        const wsTs = _wsWorkerTs.get(curSid);
        const wsNewer = wsTs !== undefined && wsTs >= _refreshStartedAt;
        // Only overwrite the WS-synced worker id when the snapshot is newer
        if (!wsNewer) currentWorkerId = matched.workerId?? null;
        const chatNameEl = document.getElementById('chatName')!;
        if (chatNameEl.style.display !== 'none') {
          if (_shouldRenderMessages(curSid, matched.history || [])) {
            // The server snapshot may lag behind what we already rendered
            // locally (optimistic user message during spawn / resume replay).
            // Rebuild only when the server reports content we don't have yet;
            // when the snapshot is just a prefix, the DOM is already correct.
            if (!_isServerHistoryPrefix(matched.history || [])) {
              renderMessages(matched.history || []);
            }
          }
        }
      }
      if (currentSessionId) {
        const wsTs = _wsWorkerTs.get(currentSessionId);
        // Skip the snapshot-based top bar refresh if a WebSocket worker update
        // already synced it while the fetch was in flight.
        if (wsTs === undefined || wsTs < _refreshStartedAt) updateTopBar();
      }
    })
    .catch(function () {
      console.warn('[refreshSessions] fetch failed, network issue');
      if (version === _refreshVersion)
        _refreshVersion--;
    });
}

// ── cbc Session Import ──

interface CbcSessionItem {
  session_id: string;
  project_dir: string;
  title: string;
  message_count: number;
  first_timestamp: string;
  last_timestamp: string;
  model: string;
  forked_from: string | null;
}

interface CbcProject {
  project_dir: string;
  session_count: number;
  resumable_count?: number;
  path_hint: string;
  drive: string;
  short_label: string;
}

interface KimiWorkspace {
  workspace_id: string;
  name: string;
  root: string;
  session_count: number;
}

interface KimiSessionItem {
  session_id: string;
  workspace_id: string;
  title: string;
  workDir: string;
  message_count: number;
  model: string;
  updatedAt: string;
}

async function fetchCbcProjects(): Promise<CbcProject[]> {
  const resp = await fetch('/api/cbc/projects');
  const data = await resp.json();
  return data.projects || [];
}

async function fetchCbcSessions(projectDir: string): Promise<CbcSessionItem[]> {
  const resp = await fetch(`/api/cbc/sessions?project_dir=${encodeURIComponent(projectDir)}`);
  const data = await resp.json();
  return data.sessions || [];
}

async function importCbcSession(sessionId: string, projectDir: string): Promise<any> {
  const resp = await fetch('/api/cbc/sessions/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, project_dir: projectDir }),
  });
  return await resp.json();
}

async function fetchKimiWorkspaces(): Promise<KimiWorkspace[]> {
  const resp = await fetch('/api/kimi/workspaces');
  const data = await resp.json();
  return data.workspaces || [];
}

async function fetchKimiSessions(cwd: string): Promise<KimiSessionItem[]> {
  const resp = await fetch(`/api/kimi/sessions?cwd=${encodeURIComponent(cwd)}`);
  const data = await resp.json();
  return data.sessions || [];
}

async function importKimiSession(sessionId: string, cwd: string): Promise<any> {
  const resp = await fetch('/api/kimi/sessions/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, cwd: cwd }),
  });
  return await resp.json();
}

function renderSessionList(): void {
  const el = document.getElementById('sessionList')!;
  el.innerHTML = '';
  modelData.forEach((s: Session) => {
    const div = document.createElement('div');
    const isSelected = _multiSelectMode && _selectedIds.has(s.id);
    div.className = 'sess-item' +
      (s.id === currentSessionId? ' active' : '') +
      (isSelected? ' selected' : '');
    div.dataset.sessionId = s.id;
    div.onclick = function (e: MouseEvent) {
      const target = e.target as HTMLElement;
      if (target.closest('.sess-del')) return;
      if (s.id.indexOf('__pending_') === 0) return; // Placeholder — not a real session yet
      if (_multiSelectMode) {
        // In multi-select mode: toggle checkbox
        const cb = div.querySelector('.sess-check') as HTMLInputElement;
        if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); }
        return;
      }
      if (document.getElementById('sessMenu')) { closeSessMenu(); return; }
      selectSession(s.id);
    };

    let lastMsg = '';
    const h = s.history || [];
    if (h.length > 0) {
      const last = h[h.length - 1];
      lastMsg = last.content || '';
      if (lastMsg.length > 40) lastMsg = lastMsg.slice(0, 40) + '\u2026';
    }

    const totalCredit = totalUsageCredit(s);
    const headerRight = _multiSelectMode
      ? '<input type="checkbox" class="sess-check"' + (isSelected? ' checked' : '') +
        ' onclick="event.stopPropagation()"' +
        ' onchange="toggleMultiSelect(\'' + s.id + '\',this.checked)"' +
        ' title="Select session">'
      : '<button class="sess-del" onclick="toggleSessMenu(event,\'' +
        s.id +
        '\')" title="Session actions" style="background:none;border:none;color:#484f58;cursor:pointer;font-size:.85rem;padding:0 2px">\u2699</button>';
    div.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:start;min-width:0">' +
      '<div class="sess-name"><span class="s-dot ' +
      (s.workerStatus || 'offline') +
      '"></span>' +
      '<span class="sess-name-text">' + esc(s.name) + '</span>' +
      '</div>' +
      headerRight +
      '</div>' +
      (lastMsg? '<div class="sess-preview">' + esc(lastMsg) + '</div>' : '') +
      '<div class="sess-meta"><span class="sess-model">' +
      esc(s.model || defaultModel()) +
      '</span>' +
      '<span>' +
      (s.historyTotal?? (s.history || []).length) +
      ' msgs</span>' +
      (totalCredit != null? '<span class="sess-credit">' + totalCredit.toFixed(2) + ' credits</span>' : '') +
      '</div>';
    el.appendChild(div);
  });
}

function totalUsageCredit(s: Session): number | null {
  if (s.totalUsage && typeof (s.totalUsage as any).credit === 'number')
    return (s.totalUsage as any).credit;
  return null;
}

function selectSession(id: string): void {
  // Save current input draft before switching
  const input = document.getElementById('chatInput') as HTMLInputElement;
  if (currentSessionId && input.value.trim()) {
    _inputDrafts.set(currentSessionId, input.value);
  } else if (currentSessionId) {
    _inputDrafts.delete(currentSessionId);
  }
  // 编辑中的出队消息：切换 session 时放弃内存编辑态（待恢复项已落盘，
  // 切回该 session 时由 _restoreInterruptedEdit 插回队列，不丢消息）
  if (_editingPending && _editingPending.sessionId !== id) {
    _editingPending = null;
  }
  currentSessionId = id;
  const s = modelData.find((x: Session) => x.id === id);
  if (!s) return;

  currentWorkerId = s.workerId?? null;
  _historyLoading = false;
  // Index of the oldest loaded message within the FULL session history.
  // The server only sends the last N (truncated) messages, so the oldest
  // loaded message sits at historyTotal - loaded, NOT at loaded.
  const loaded = (s.history || []).length;
  _historyLoadEnd = Math.max(0, (s.historyTotal?? loaded) - loaded);

  renderSessionList();
  updateTopBar();
  renderMessages(s.history || []);
  // 切回 session：恢复上次被中断编辑的待恢复项（刷新 / 切换遗留），再渲染队列
  _restoreInterruptedEdit(id);
  // 切换 session：从 localStorage 恢复该会话的发送队列并尝试自动发送
  renderQueuePanel();
  updateQueueBadge();
  flushQueue();
  // 重新选中同一 session 且仍在编辑：重绘后恢复编辑框焦点
  if (_editingPending && _editingPending.sessionId === id) {
    const listEl = document.getElementById('queueList');
    const ta = listEl ? listEl.querySelector<HTMLTextAreaElement>('.queue-row.editing textarea') : null;
    if (ta) ta.focus();
  }
  // Restore input draft for this session
  input.value = _inputDrafts.get(id) || '';
  const settingsBtn = document.getElementById('settingsBtn')!;
  settingsBtn.style.display = '';
  // sync panel if it's already open (may need to switch adapter config first)
  if (document.getElementById('settingsPanel')!.classList.contains('open')) {
    syncPanelFromServer();
  }
  // Load additional history if truncated
  if (s.historyTruncated) {
    _loadOlderToBottom = true;
    loadOlderMessages();
  }
}

/** Fetch and prepend older messages for the current session. */
function loadOlderMessages(): void {
  if (_historyLoading || _historyLoadEnd <= 0 || !currentSessionId) return;
  _historyLoading = true;
  const sid = currentSessionId;
  const limit = 50;
  fetch('/api/sessions/' + sid + '/history?before=' + _historyLoadEnd + '&limit=' + limit)
    .then((r: Response) => r.json())
    .then((d: any) => {
      _historyLoading = false;
      if (currentSessionId !== sid) return;
      if (d.error) return;
      const msgs: Message[] = d.history || d.messages || [];
      if (msgs.length === 0) return;
      _historyLoadEnd = d.start;      // Build fragment for older messages
      const frag = document.createDocumentFragment();
      const grouped: Array<{ type?: string; items?: Message[] } & Partial<Message>> = [];
      let toolGroup: any = null;
      for (let i = 0; i < msgs.length; i++) {
        const h = msgs[i];
        if (h.role === 'tool') {
          if (!toolGroup) {
            toolGroup = { type: 'tool_group', items: [] };
            grouped.push(toolGroup);
          }
          toolGroup.items!.push(h);
        } else {
          toolGroup = null;
          grouped.push(h);
        }
      }
      for (let i = 0; i < grouped.length; i++) {
        const g = grouped[i];
        if (g.type === 'tool_group') {
          _renderToolGroup(g.items!, frag);
        } else {
          _renderMsgEl(g.role || '', g.content || '', frag);
        }
      }
      const el = document.getElementById('messages')!;
      // Preserve scroll: anchor to first visible element
      const ref = el.firstElementChild;
      const scrollRefTop = ref? ref.getBoundingClientRect().top: 0;
      if (el.firstChild) {
        el.insertBefore(frag, el.firstChild);
      } else {
        el.appendChild(frag);
      }
      // Restore scroll position so visible content stays put,
      // UNLESS this is the initial load after session switch (then scroll to bottom).
      if (_loadOlderToBottom) {
        _loadOlderToBottom = false;
        scrollToBottom();
      } else if (ref) {
        el.scrollTop += ref.getBoundingClientRect().top - scrollRefTop;
      }
      // Update modelData
      const s = modelData.find((x: Session) => x.id === sid);
      if (s) {
        s.history = msgs.concat(s.history);
        if (d.start <= 0) s.historyTruncated = false;
      }
      // Trim from bottom if DOM nodes exceed limit (user is near top anyway)
      let nodeCount = el.children.length;
      if (nodeCount > MAX_MESSAGE_NODES) {
        const trimCount = nodeCount - MAX_MESSAGE_NODES;
        for (let i = 0; i < trimCount; i++) {
          const last = el.lastElementChild;
          if (last) el.removeChild(last);
        }
      }
    })
    .catch(function () {
      // Network failure: reset the loading flag so future scrolls can retry,
      // otherwise lazy-loading would be stuck forever.
      _historyLoading = false;
    });
}

// ── Top bar ──

function updateTopBar(): void {
  const s = modelData.find((x: Session) => x.id === currentSessionId);
  if (!s) {
    showEmpty();
    return;
  }
  (document.getElementById('emptyHint')!).style.display = 'none';
  (document.getElementById('chatName')!).style.display = '';
  (document.getElementById('chatModel')!).style.display = '';
  (document.getElementById('chatName')!).textContent =
    s.name || (currentSessionId?? '').slice(0, 12);
  (document.getElementById('chatModel')!).textContent = s.model || defaultModel();
  const sidsEl = document.getElementById('chatSessionIds')!;
  sidsEl.style.display = 'flex';
  var sesId = s.id || '';
  var cbcId = s.cliSessionId;
  sidsEl.innerHTML =
    '<span class="sid-item">' +
    esc(sesId.slice(0, 12)) +
    '<button class="sid-copy" title="Copy session ID" onclick="copyToClipboard(\'' + sesId + '\')">\u29C9</button>' +
    '</span>' +
    (cbcId?
      '<span class="sid-item">' +
      esc(cbcId.slice(0, 8)) +
      '<button class="sid-copy" title="Copy cbc session ID" onclick="copyToClipboard(\'' + cbcId + '\')">\u29C9</button>' +
      '</span>'
      : '');
  const status = s.workerStatus || 'offline';
  (document.getElementById('chatStatus')!).textContent =
    status + (currentWorkerId? ' (' + currentWorkerId + ')' : ' (no worker)');
  const dot = document.getElementById('mobileWorkerDot');
  if (dot) dot.className = 's-dot ' + status;
}

function showEmpty(): void {
  (document.getElementById('emptyHint')!).style.display = '';
  (document.getElementById('chatName')!).style.display = 'none';
  (document.getElementById('chatModel')!).style.display = 'none';
  (document.getElementById('chatSessionIds')!).style.display = 'none';
  (document.getElementById('chatStatus')!).textContent = '';
  const dot = document.getElementById('mobileWorkerDot');
  if (dot) dot.className = 's-dot offline';
  (document.getElementById('settingsBtn')!).style.display = 'none';
  (document.getElementById('settingsPanel')!).className = '';
  (document.getElementById('messages')!).innerHTML =
    '<div class="empty-chat">Select a session to start</div>';
  // 无会话时收起发送队列面板、重置角标与滚动按钮位置
  const qp = document.getElementById('queuePanel');
  if (qp) qp.classList.remove('open');
  const qBtn = document.getElementById('queueToggleBtn');
  if (qBtn) qBtn.classList.remove('open');
  updateQueueBadge();
  const scrollBtn = document.getElementById('scrollBottomBtn');
  if (scrollBtn) scrollBtn.style.bottom = '70px';
  currentHistory = [];
  _recordRenderedFor(currentSessionId, currentHistory);
  toolGroupOpen = false;
}

// ── Scroll helpers ──

const SCROLL_BOTTOM_THRESHOLD = 120;
const MAX_MESSAGE_NODES = 2000;
const RENDER_CHUNK = 30;

function isNearBottom(): boolean {
  const el = document.getElementById('messages')!;
  const last = el.lastElementChild;
  if (!last) return true;
  // Measure from real element geometry instead of scrollHeight:
  // content-visibility:auto skips layout for off-screen messages, so
  // scrollHeight underestimates the true content height and makes the
  // distance to the bottom look smaller than it is (false near-bottom).
  return last.getBoundingClientRect().bottom - el.getBoundingClientRect().bottom <= SCROLL_BOTTOM_THRESHOLD;
}

function scrollMessages(): void {
  if (isNearBottom()) {
    scrollToBottom();
  } else {
    updateScrollToBottomBtn();
  }
}

function updateScrollToBottomBtn(): void {
  const btn = document.getElementById('scrollBottomBtn') as HTMLButtonElement;
  if (!btn) return;
  btn.style.display = isNearBottom() ? 'none' : '';
}

// Guards against overlapping alignment loops from rapid scrollToBottom calls
// (e.g. streaming) — a newer call supersedes any in-flight one.
let _scrollToBottomVersion = 0;

function scrollToBottom(): void {
  const el = document.getElementById('messages')!;
  const last = el.lastElementChild;
  if (!last) {
    el.scrollTop = el.scrollHeight;
    updateScrollToBottomBtn();
    return;
  }
  const version = ++_scrollToBottomVersion;
  let guard = 0;
  let prevTop = el.scrollTop;
  const align = (): void => {
    if (version !== _scrollToBottomVersion) return; // superseded
    const target = el.lastElementChild;
    if (!target) { updateScrollToBottomBtn(); return; }
    // scrollIntoView scrolls from real element geometry, so it lands on the
    // true bottom even though content-visibility:auto makes scrollHeight and
    // element positions unreliable.  The first pass may target an estimated
    // position before the last message is laid out; the browser re-layouts
    // shortly after (setTimeout, not rAF, so it also runs when no frames are
    // being produced).  Keep re-aligning (bounded) until the scroll position
    // settles AND the last message actually sits at the bottom edge.
    target.scrollIntoView({ block: 'end' });
    setTimeout(() => {
      if (version !== _scrollToBottomVersion) return;
      const t = el.lastElementChild;
      if (!t) { updateScrollToBottomBtn(); return; }
      const cur = el.scrollTop;
      const gap = el.getBoundingClientRect().bottom - t.getBoundingClientRect().bottom;
      const moving = Math.abs(cur - prevTop) > 2;
      const misaligned = Math.abs(gap) > 2;
      if ((moving || misaligned) && guard++ < 8) {
        prevTop = cur;
        align();
      } else {
        updateScrollToBottomBtn();
      }
    }, 16);
  };
  align();
}

// ── Messages ──

function renderMessages(history: Message[]): void {
  currentHistory = history || [];
  _recordRenderedFor(currentSessionId, currentHistory);
  _currentToolGroupStart = -1;
  _rendering = true;
  const el = document.getElementById('messages')!;
  el.innerHTML = '';
  el.scrollTop = 0;
  if (!currentHistory || currentHistory.length === 0) {
    el.innerHTML =
      '<div class="empty-chat">No messages yet. Start a conversation.</div>';
    _rendering = false;
    toolGroupOpen = false;
    return;
  }
  const grouped: Array<{ type?: string; items?: Message[] } & Partial<Message>> = [];
  let toolGroup: any = null;
  for (let i = 0; i < currentHistory.length; i++) {
    const h = currentHistory[i];
    if (h.role === 'tool') {
      if (!toolGroup) {
        toolGroup = { type: 'tool_group', items: [] };
        grouped.push(toolGroup);
      }
      toolGroup.items!.push(h);
    } else {
      toolGroup = null;
      grouped.push(h);
    }
  }

  function finishRender(): void {
    _rendering = false;
    scrollToBottom();
    toolGroupOpen = false;
  }

  // Fast path: small sessions render synchronously
  if (grouped.length <= RENDER_CHUNK) {
    const frag = document.createDocumentFragment();
    for (let i = 0; i < grouped.length; i++) {
      const g = grouped[i];
      if (g.type === 'tool_group') {
        _renderToolGroup(g.items!, frag);
      } else {
        _renderMsgEl(g.role || '', g.content || '', frag);
      }
    }
    el.appendChild(frag);
    finishRender();
    return;
  }
  // Chunked path: first chunk sync, rest via timeout. Only scroll once at the
  // end to avoid per-chunk reflows and visual jumping.
  _renderVersion++;
  const version = _renderVersion;
  let index = 0;

  function renderNextChunk(): void {
    if (version !== _renderVersion) return;
    const end = Math.min(index + RENDER_CHUNK, grouped.length);
    const frag = document.createDocumentFragment();
    for (let i = index; i < end; i++) {
      const g = grouped[i];
      if (g.type === 'tool_group') {
        _renderToolGroup(g.items!, frag);
      } else {
        _renderMsgEl(g.role || '', g.content || '', frag);
      }
    }
    el.appendChild(frag);
    index = end;
    if (index < grouped.length) {
      setTimeout(renderNextChunk, 0);
    } else {
      finishRender();
    }
  }

  // First chunk renders synchronously for immediate visibility
  {
    const end = Math.min(RENDER_CHUNK, grouped.length);
    const frag = document.createDocumentFragment();
    for (let i = 0; i < end; i++) {
      const g = grouped[i];
      if (g.type === 'tool_group') {
        _renderToolGroup(g.items!, frag);
      } else {
        _renderMsgEl(g.role || '', g.content || '', frag);
      }
    }
    el.appendChild(frag);
    index = end;
  }

  if (index < grouped.length) {
    setTimeout(renderNextChunk, 0);
  } else {
    finishRender();
  }
}

function formatToolContent(content: string): string {
  if (!content)
    return '\uD83D\uDD27 <em>(empty)</em>';
  let legacyMatch = content.match(/^tool call:\s*(.+?)(?:\r?\n|\r)args:\s*([\s\S]*)$/);
  if (legacyMatch) {
    const name = legacyMatch[1].trim();
    const jsonText = legacyMatch[2].trim();
    const formatted = formatToolArgs(jsonText);
    if (!formatted || !formatted.trim())
      return '\uD83D\uDD27 <strong>' + esc(name) + '</strong>';
    return '\uD83D\uDD27 <strong>' + esc(name) + '</strong>' +
      '<div class="tool-pre">' + esc(formatted) + '</div>';
  }
  const match = content.match(/^([^(]+)\(([\s\S]*)\)$/);
  if (!match)
    return '\uD83D\uDD27 ' + esc(content).replace(/\n/g, '<br>');
  let name = (match[1] || '').trim();
  const jsonText = match[2] || '';
  if (!name) name = 'tool';
  const formatted = formatToolArgs(jsonText);
  if (!formatted || !formatted.trim())
    return '\uD83D\uDD27 <strong>' + esc(name) + '</strong>';
  return '\uD83D\uDD27 <strong>' + esc(name) + '</strong>' +
    '<div class="tool-pre">' + esc(formatted) + '</div>';
}

function formatToolArgs(jsonText: string): string {
  try {
    const parsed = JSON.parse(jsonText);
    if (parsed && typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
      const cleaned: Record<string, unknown> = {};
      Object.keys(parsed).forEach(function (key: string) {
        if (key === '_comment' || key === '$comment' || key === '-comment') return;
        cleaned[key] = parsed[key];
      });
      return JSON.stringify(cleaned, null, 2);
    }
    return jsonText;
  } catch (e) {
    return jsonText;
  }
}

function toolName(content: string): string {
  if (!content) return '(empty)';
  const callMatch = content.match(/^tool call:\s*(.+)/);
  if (callMatch) return callMatch[1].split('\n')[0].trim();
  const resultMatch = content.match(/^tool result \(([^)]+)\)/);
  if (resultMatch) return resultMatch[1].trim();
  const idx = content.indexOf('(');
  if (idx >= 0) return content.slice(0, idx).trim();
  return content.split('\n')[0].trim().slice(0, 30);
}

function _renderMsgEl(role: string, content: string, parent?: Node): void {
  // a non-tool message closes any open streaming tool-group
  if (role !== 'tool') _currentToolGroupStart = -1;
  const el = parent || document.getElementById('messages')!;
  const div = document.createElement('div');
  if (role === 'user') {
    div.className = 'msg user';
    div.innerHTML = '<div class="msg-content">' + renderMarkdown(content) + '</div>';
  } else if (role === 'assistant') {
    div.className = 'msg assistant';
    div.innerHTML = '<div class="msg-content">' + renderMarkdown(content) + '</div>';
  } else if (role === 'thinking') {
    div.className = 'msg thinking';
    div.innerHTML =
      '\uD83D\uDCAD <span class="thinking-toggle">show thinking</span>' +
      ' <span class="toggle-icon">\u25BC</span>' +
      (_getUnread().has(content) ? '<span class="unread-badge"></span>' : '') +
      '<div class="thinking-body">' + esc(content) + '</div>';
    div.onclick = function () {
      const body = div.querySelector('.thinking-body') as HTMLElement;
      const toggle = div.querySelector('.thinking-toggle') as HTMLElement;
      const badge = div.querySelector('.unread-badge') as HTMLElement;
      if (!body || !toggle) return;
      body.classList.toggle('open');
      div.classList.toggle('open');
      toggle.textContent = body.classList.contains('open') ? 'hide thinking' : 'show thinking';
      if (badge) { badge.style.display = 'none'; _getUnread().delete(content); }
    };
  } else if (role === 'tool') {
    div.className = 'msg tool';
    div.innerHTML = formatToolContent(content);
  } else {
    div.className = 'msg system';
    div.textContent = content || '';
  }
  el.appendChild(div);
}

function _renderToolGroup(items: Message[], parent?: Node): void {
  const wrapper = _createToolGroupEl(items);
  const el = parent || document.getElementById('messages')!;
  el.appendChild(wrapper);
}

/** Build a tool-group element (header + body) for the given items.
 *  Used by both full re-render (renderMessages) and live streaming (appendEvent). */
function _createToolGroupEl(items: Message[]): HTMLElement {
  const wrapper = document.createElement('div');
  wrapper.className = 'tool-group collapsed';
  const count = items.length;
  let names = items.map(function (t) { return toolName(t.content); }).slice(0, 3).join(', ');
  if (items.length > 3) names += ', \u2026';
  const hasUnread = items.some(function (t: Message) { return _getUnread().has(t.content); });
  wrapper.innerHTML =
    '<div class="tool-group-header">' +
    '\uD83D\uDD27 <strong>' + count + ' tools:</strong> ' +
    esc(names) +
    ' <span class="toggle-icon">\u25BC</span>' +
    (hasUnread? '<span class="unread-badge"></span>' : '') +
    '</div>' +
    '<div class="tool-group-body"></div>';
  wrapper.setAttribute('data-tool-contents', JSON.stringify(items.map(function (t: Message) { return t.content; })));
  const body = wrapper.querySelector('.tool-group-body')!;
  _fillToolGroupBody(body, items);
  (wrapper.querySelector('.tool-group-header') as HTMLElement).onclick = function () {
    wrapper.classList.toggle('collapsed');
    const badge = wrapper.querySelector('.unread-badge') as HTMLElement;
    if (badge) { badge.style.display = 'none'; }
    try {
      const contents: string[] = JSON.parse(wrapper.getAttribute('data-tool-contents') || '[]');
      contents.forEach(function (c: string) { _getUnread().delete(c); });
    } catch (e) { /* ignore */ }
  };
  return wrapper;
}

/** Append tool message divs into an existing tool-group body. */
function _fillToolGroupBody(body: Element, items: Message[]): void {
  items.forEach(function (t) {
    const toolDiv = document.createElement('div');
    toolDiv.className = 'msg tool';
    toolDiv.innerHTML = formatToolContent(t.content);
    body.appendChild(toolDiv);
  });
}

/** Find the currently-open streaming tool-group (last tool-group in #messages,
 *  created by appendEvent). Returns null if the last child isn't a tool-group. */
function _lastToolGroupEl(): HTMLElement | null {
  const el = document.getElementById('messages')!;
  const children = el.children;
  if (children.length === 0) return null;
  const last = children[children.length - 1];
  if (last.classList.contains('tool-group')) return last as HTMLElement;
  return null;
}

function addMessage(role: string, content: string): void {
  currentHistory.push({ role: role, content: content });
  _recordRenderedFor(currentSessionId, currentHistory);
  if (role === 'thinking' || role === 'tool') _getUnread().add(content);
  const el = document.getElementById('messages')!;
  // First message on an empty session: drop the "No messages yet" placeholder
  // so it doesn't linger above the optimistic message.
  const empty = el.querySelector('.empty-chat');
  if (empty) empty.remove();
  _renderMsgEl(role, content);
  scrollMessages();
}

function appendEvent(event: WorkerEvent): void {
  const t = event.type;
  if (t === 'system' && event.subtype === 'init') return;
  if (t === 'result') return;
  if (t === 'assistant') {
    const content = (event.message && event.message.content) || [];
    content.forEach((b: WorkerEventContent) => {
      if (b.type === 'text') {
        currentHistory.push({ role: 'assistant', content: b.text || '' });
        _renderMsgEl('assistant', b.text || '');
      } else if (b.type === 'thinking') {
        currentHistory.push({ role: 'thinking', content: b.thinking || '' });
        _getUnread().add(b.thinking || '');
        _renderMsgEl('thinking', b.thinking || '');
      } else if (b.type === 'tool_use') {
        const c = (b.name || '') + '(' + JSON.stringify(b.input || {}) + ')';
        currentHistory.push({ role: 'tool', content: c });
        _getUnread().add(c);
        _appendToolMessage(c);
      }
    });
    _recordRenderedFor(currentSessionId, currentHistory);
    scrollMessages();
  }
}

/** Render a tool message during live streaming, grouped under a
 *  tool-group-header so the header is always shown alongside the blocks.
 *  Consecutive tool blocks are appended into the same open tool-group;
 *  a non-tool message closes the current group. */
function _appendToolMessage(content: string): void {
  const el = document.getElementById('messages')!;
  const lastGroup = _lastToolGroupEl();
  if (lastGroup) {
    // append into the existing streaming group
    const body = lastGroup.querySelector('.tool-group-body')!;
    const count = body.children.length + 1;
    _fillToolGroupBody(body, [{ role: 'tool', content: content }]);
    // refresh header count + names, always show badge for new streaming tool
    const allTools = currentHistory
      .slice(_currentToolGroupStart)
      .filter((m: Message) => m.role === 'tool');
    const names = allTools.map(function (m) { return toolName(m.content); }).slice(0, 3).join(', ');
    const headerHtml =
      '\uD83D\uDD27 <strong>' + count + ' tools:</strong> ' +
      esc(names) + (count > 3 ? ', \u2026' : '') +
      ' <span class="toggle-icon">\u25BC</span>' +
      '<span class="unread-badge"></span>';
    (lastGroup.querySelector('.tool-group-header') as HTMLElement).innerHTML = headerHtml;
    lastGroup.setAttribute('data-tool-contents', JSON.stringify(allTools.map(function (m: Message) { return m.content; })));
    return;
  }
  // start a new tool-group
  _currentToolGroupStart = currentHistory.length - 1;
  const wrapper = _createToolGroupEl([{ role: 'tool', content: content }]);
  el.appendChild(wrapper);
}

function appendResult(d: StreamEvent): void {
  const status = d.status === 'error' ? 'error' : 'done';
  addMessage('system', '[' + status.toUpperCase() + '] Task completed');
}

// ── Settings panel ──

function toggleSettings(): void {
  const panel = document.getElementById('settingsPanel')!;
  const btn = document.getElementById('settingsBtn')!;
  const isOpen = panel.classList.toggle('open');
  btn.classList.toggle('open', isOpen);
  if (isOpen) {
    if (!_adapterConfigReady)
      toast('Loading settings…');
    syncPanelFromServer();
  }
}

/** Sync the settings panel fields to the current session's server-side values.
 *  Called when the panel opens or the session switches. */
function syncPanelFromServer(): void {
  const s = modelData.find((x: Session) => x.id === currentSessionId);
  if (!s) return;

  // If session uses a different adapter, load its config first
  const sessAdapter = s.adapter || 'cbc';
  if (sessAdapter !== currentAdapter) {
    _adapterConfigReady = false;
    loadAdapterConfig(sessAdapter);
    return;
  }

  // wait until all selects are populated (async adapter config fetch)
  if ((document.getElementById('settingModel') as HTMLSelectElement).getAttribute('data-loaded') !== '1') return;
  if (!_adapterConfigReady) return;

  updateSettingsVisibility();

  const sel = document.getElementById('settingModel') as HTMLSelectElement;
  const model = s.model || defaultModel();
  sel.value = allModels().indexOf(model) >= 0 ? model : defaultModel();

  if (supportsSetting('permissionMode')) {
    (document.getElementById('settingMode') as HTMLSelectElement).value =
      s.permissionMode || defaultPermissionMode();
  }
  if (supportsSetting('thinking')) {
    (document.getElementById('settingThinking') as HTMLInputElement).checked =
      s.alwaysThinkingEnabled || false;
  }
  if (supportsSetting('effort')) {
    (document.getElementById('settingEffort') as HTMLSelectElement).value =
      effortValues().indexOf(s.effort) >= 0 ? s.effort: (effortValues()[1] || effortValues()[0] || '');
  }
  (document.getElementById('effortGroup')!).style.display =
    (supportsSetting('thinking') && supportsSetting('effort') && s.alwaysThinkingEnabled && effortValues().length > 0) ? '' : 'none';

  // record the baseline so we can detect pending changes
  lastSyncedSettings = {
    model: getSettingModel(),
    permissionMode: supportsSetting('permissionMode')
      ? (document.getElementById('settingMode') as HTMLSelectElement).value
      : '',
    alwaysThinkingEnabled: supportsSetting('thinking')
      ? (document.getElementById('settingThinking') as HTMLInputElement).checked
      : false,
    effort: supportsSetting('effort')
      ? (document.getElementById('settingEffort') as HTMLSelectElement).value
      : '',
  };
  updateSetButtonVisibility();
}

/** Returns true when any panel field differs from lastSyncedSettings. */
function hasPendingChanges(): boolean {
  if (!lastSyncedSettings) return false;
  if (supportsSetting('model') && getSettingModel() !== lastSyncedSettings.model) return true;
  if (supportsSetting('permissionMode') &&
      (document.getElementById('settingMode') as HTMLSelectElement).value !== lastSyncedSettings.permissionMode) return true;
  if (supportsSetting('thinking') &&
      (document.getElementById('settingThinking') as HTMLInputElement).checked !== lastSyncedSettings.alwaysThinkingEnabled) return true;
  if (supportsSetting('effort') &&
      (document.getElementById('settingEffort') as HTMLSelectElement).value !== lastSyncedSettings.effort) return true;
  return false;
}

function getSettingModel(): string {
  const sel = document.getElementById('settingModel') as HTMLSelectElement;
  if (sel.value === '__custom__') {
    const inp = document.getElementById(
      'settingModelCustom'
    ) as HTMLInputElement;
    return inp.value.trim() || defaultModel();
  }
  return sel.value || defaultModel();
}

/** Show/hide the Apply Settings button based on whether settings differ from
 *  the last synced state. */
function updateSetButtonVisibility(): void {
  const btn = document.getElementById('applySettingsBtn')!;
  btn.style.display = hasPendingChanges() ? '' : 'none';
}

/** Called when the Think checkbox is toggled: show/hide Effort + auto-select
 *  medium, then update the Set button.  No API call is made. */
function onThinkingToggle(): void {
  if (!supportsSetting('thinking')) return;
  const thinking = (document.getElementById('settingThinking') as HTMLInputElement).checked;
  (document.getElementById('effortGroup')!).style.display =
    (supportsSetting('effort') && thinking && effortValues().length > 0) ? '' : 'none';
  if (supportsSetting('effort') && thinking && effortValues().length > 0) {
    const eff = document.getElementById('settingEffort') as HTMLSelectElement;
    if (!eff.value || eff.value === effortValues()[0])
      eff.value = effortValues()[1] || effortValues()[0];
  }
  updateSetButtonVisibility();
}

/** Apply all pending model/mode/thinking/effort changes via a single API call.
 *  Handles both the "no worker" (PATCH session) and "worker exists" cases. */
function applySettings(): void {
  if (!currentSessionId) return;

  // Worker-level settings can only be changed when idle.
  // Use Restart to apply settings + respawn while worker is running/held.
  const s = modelData.find((x: Session) => x.id === currentSessionId);
  if (s && (s.workerStatus === 'running' || s.workerStatus === 'held')) {
    toast('Cannot change settings while worker is busy. Use Restart instead.');
    return;
  }

  if (!currentWorkerId) {
    // no worker: persist settings to session via PATCH
    fetch('/api/sessions/' + currentSessionId, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_buildSettingsBody()),
    })
      .then((r: Response) => r.json())
      .then((d: ApiGenericResponse) => {
        if (d.error) { toast(d.error); return; }
        markSettingsApplied();
      });
    return;
  }

  _postWorkerSettings();
}

/** Build the settings payload object from panel values. */
function _buildSettingsBody(): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (supportsSetting('model')) body.model = getSettingModel();
  if (supportsSetting('permissionMode')) {
    const mode = (document.getElementById('settingMode') as HTMLSelectElement).value;
    if (mode) body.permissionMode = mode;
  }
  if (supportsSetting('thinking'))
    body.alwaysThinkingEnabled = (document.getElementById('settingThinking') as HTMLInputElement).checked;
  if (supportsSetting('effort'))
    body.effort = (document.getElementById('settingEffort') as HTMLSelectElement).value;
  return body;
}

/** POST current panel settings to the worker (always allowed, triggers respawn). */
function _postWorkerSettings(): void {
  fetch('/api/worker/' + currentWorkerId + '/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(_buildSettingsBody()),
  })
    .then((r: Response) => r.json())
    .then((d: ApiGenericResponse) => {
      if (d.error) { toast(d.error); return; }
      markSettingsApplied();
    });
}

/** Called after successful settings apply — update baseline and hide button. */
function markSettingsApplied(): void {
  lastSyncedSettings = {
    model: supportsSetting('model') ? getSettingModel() : '',
    permissionMode: supportsSetting('permissionMode') ? (document.getElementById('settingMode') as HTMLSelectElement).value : '',
    alwaysThinkingEnabled: supportsSetting('thinking') ? (document.getElementById('settingThinking') as HTMLInputElement).checked : false,
    effort: supportsSetting('effort') ? (document.getElementById('settingEffort') as HTMLSelectElement).value : '',
  };
  updateSetButtonVisibility();
}

// ── Worker actions (restart / interrupt / takeover / kill) ──

function restartWorker(): void {
  if (currentWorkerId) {
    // Restart is always allowed — post panel settings + respawn, no busy check.
    _postWorkerSettings();
  } else {
    const body = _buildSettingsBody();
    body.sessionId = currentSessionId;
    fetch('/api/spawn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then((r: Response) => r.json())
      .then((d: ApiGenericResponse) => {
        if (d.error) {
          toast('Spawn failed: ' + d.error);
          return;
        }
        currentWorkerId = d.workerId?? null;
        updateTopBar();
      });
  }
}

function interruptWorker(): void {
  if (!currentWorkerId) {
    toast('No worker running');
    return;
  }
  fetch('/api/worker/' + currentWorkerId + '/interrupt', { method: 'POST' })
    .then((r: Response) => r.json())
    .then((d: ApiGenericResponse) => {
      if (d.error) toast(d.error);
    });
}

function takeover(): void {
  if (!currentWorkerId) {
    toast('No worker running');
    return;
  }
  fetch('/api/worker/' + currentWorkerId + '/takeover', { method: 'POST' })
    .then((r: Response) => r.json())
    .then((d: ApiGenericResponse) => {
      if (d.error) {
        toast(d.error);
        return;
      }
      navigator.clipboard
        .writeText(d.takeoverCommand ?? ('cbc --resume ' + (d.cliSessionId?? '')))
        .then(() => {
          toast('PowerShell opened. Session copied to clipboard.');
        })
        .catch(() => {
          toast('PowerShell opened for takeover.');
        });
    });
}

function takeoverMobile(): void {
  if (!currentWorkerId) {
    toast('No worker running');
    return;
  }
  fetch('/api/worker/' + currentWorkerId + '/takeover-command')
    .then((r: Response) => r.json())
    .then((d: any) => {
      if (d.error) {
        toast(d.error);
        return;
      }
      navigator.clipboard
        .writeText(d.takeoverCommand ?? ('cbc --resume ' + (d.cliSessionId?? '')))
        .then(() => {
          toast('Takeover command copied to clipboard.');
        })
        .catch(() => {
          toast('Failed to copy command. Manual: ' + (d.takeoverCommand || ''));
        });
    })
    .catch(() => {
      toast('Failed to get takeover command.');
    });
}

function killWorker(): void {
  if (!currentWorkerId) {
    toast('No worker running');
    return;
  }
  if (!confirm('Kill worker ' + currentWorkerId + '?')) return;
  const deadId = currentWorkerId;
  currentWorkerId = null;
  updateTopBar();
  fetch('/api/kill/' + deadId, { method: 'POST' })
    .then((r: Response) => r.json())
    .then((d: ApiGenericResponse) => {
      if (d.error) toast(d.error);
    });
}

// ── Send queue (client-side, localStorage per session) ──

function _genQueueId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

function loadQueue(sessionId: string): QueuedMessage[] {
  try {
    const raw = localStorage.getItem(_QUEUE_KEY_PREFIX + sessionId);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(function (x: unknown): x is Record<string, unknown> {
        return !!x && typeof x === 'object' && typeof (x as Record<string, unknown>).text === 'string';
      })
      .map(function (x): QueuedMessage {
        const anyX = x as Record<string, unknown>;
        return {
          id: typeof anyX.id === 'string' ? anyX.id : _genQueueId(),
          text: String(anyX.text),
          createdAt: typeof anyX.createdAt === 'number' ? anyX.createdAt : Date.now(),
          status: 'pending',
        };
      })
      .slice(0, _QUEUE_MAX);
  } catch (e) {
    return [];
  }
}

function getQueue(sessionId: string): QueuedMessage[] {
  let q = _queueCache.get(sessionId);
  if (q) return q;
  q = loadQueue(sessionId);
  _queueCache.set(sessionId, q);
  return q;
}

function persistQueue(sessionId: string, queue: QueuedMessage[]): void {
  _queueCache.set(sessionId, queue.slice());
  try {
    localStorage.setItem(_QUEUE_KEY_PREFIX + sessionId, JSON.stringify(queue));
  } catch (e) {
    console.warn('[sendQueue] persist failed', e);
  }
}

/** 删除 session 对应的队列存储（session 删除时清理孤儿 key）。
 *  同时清理该 session 的编辑待恢复记录，并放弃指向它的内存编辑态。 */
function _removeQueueStorage(sessionId: string): void {
  _queueCache.delete(sessionId);
  try {
    localStorage.removeItem(_QUEUE_KEY_PREFIX + sessionId);
    localStorage.removeItem(_BATCH_KEY_PREFIX + sessionId);
  } catch (e) { /* ignore */ }
  if (_editingPending && _editingPending.sessionId === sessionId) _editingPending = null;
  _clearEditPendingStorage(sessionId);
}

// ── 批量拼接发送开关（per-session，localStorage 布尔）──

function _batchKey(sessionId: string): string {
  return _BATCH_KEY_PREFIX + sessionId;
}

function isBatchEnabled(sessionId: string): boolean {
  try {
    return localStorage.getItem(_batchKey(sessionId)) === '1';
  } catch (e) {
    return false;
  }
}

function setBatchEnabled(sessionId: string, on: boolean): void {
  try {
    if (on) localStorage.setItem(_batchKey(sessionId), '1');
    else localStorage.removeItem(_batchKey(sessionId));
  } catch (e) { /* ignore */ }
}

/** 勾选/取消"拼接发送"：状态按当前 session 立即持久化（渲染时由 renderQueuePanel 恢复）。 */
function onBatchToggle(): void {
  if (!currentSessionId) return;
  const cb = document.getElementById('queueBatchToggle') as HTMLInputElement | null;
  if (!cb) return;
  setBatchEnabled(currentSessionId, cb.checked);
}

// ── 编辑中出队消息的待恢复记录（localStorage 按 session 持久化）──
// 编辑开始时消息即从队列出队；若此时刷新页面或切换 session，编辑态随内存丢失，
// 靠这份记录把消息插回队列，避免"编辑到一半页面刷新 → 消息丢失"。

/** 待恢复记录落盘（原文 + 原位置；草稿 draftText 只存内存，不落盘）。 */
function _persistEditPending(p: QueuedEditPending): void {
  try {
    localStorage.setItem(_QUEUE_EDIT_KEY_PREFIX + p.sessionId, JSON.stringify({
      id: p.id,
      text: p.originalText,
      index: p.index,
      createdAt: p.createdAt,
    }));
  } catch (e) {
    console.warn('[sendQueue] persist edit pending failed', e);
  }
}

/** 清除 session 的待恢复记录（保存/取消/删除 session 时）。 */
function _clearEditPendingStorage(sessionId: string): void {
  try {
    localStorage.removeItem(_QUEUE_EDIT_KEY_PREFIX + sessionId);
  } catch (e) { /* ignore */ }
}

/** 读取 session 的待恢复项（被中断的编辑，刷新/切换 session 后由 _restoreInterruptedEdit 插回）。 */
function _loadEditPending(sessionId: string): QueuedEditPending | null {
  try {
    const raw = localStorage.getItem(_QUEUE_EDIT_KEY_PREFIX + sessionId);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    const d = parsed as Record<string, unknown>;
    if (typeof d.id !== 'string' || typeof d.text !== 'string') return null;
    return {
      id: d.id,
      sessionId: sessionId,
      index: typeof d.index === 'number' ? d.index : 0,
      originalText: d.text,
      draftText: d.text,
      createdAt: typeof d.createdAt === 'number' ? d.createdAt : Date.now(),
    };
  } catch (e) {
    return null;
  }
}

/** 入队：trim 校验 → 上限校验 → push → 持久化 → 刷新面板/角标。
 *  入队不上屏（不调用 addMessage），避免"已发"假象。 */
function enqueueMessage(text: string): void {
  if (!currentSessionId) return;
  const sid = currentSessionId;
  const queue = getQueue(sid);
  if (queue.length >= _QUEUE_MAX) {
    toast('发送队列已满（上限 ' + _QUEUE_MAX + ' 条）');
    return;
  }
  const item: QueuedMessage = {
    id: _genQueueId(),
    text: text,
    createdAt: Date.now(),
    status: 'pending',
  };
  queue.push(item);
  persistQueue(sid, queue);
  const input = document.getElementById('chatInput') as HTMLInputElement;
  input.value = '';
  _inputDrafts.delete(sid);
  renderQueuePanel();
  updateQueueBadge();
  toast('已加入发送队列（' + queue.length + ' 条待发）');
}

function removeQueued(id: string): void {
  if (!currentSessionId) return;
  const sid = currentSessionId;
  const queue = getQueue(sid);
  const idx = queue.findIndex(function (x) { return x.id === id; });
  if (idx < 0) return;
  queue.splice(idx, 1);
  persistQueue(sid, queue);
  renderQueuePanel();
  updateQueueBadge();
  toast('已从发送队列删除');
}

/** 把编辑中的出队消息插回队列，返回实际插入位置。
 *  原位置仍有效（index <= 队列长度）则插回原位置；
 *  否则编辑期间队列已变化（前面的项被发走/删除、队列被清空等），插入队首——此时该消息
 *  已比原位置更靠前，保存/取消后让它尽快被处理（自动 flush 从队首取）更符合用户意图。 */
function _reinsertQueued(p: QueuedEditPending, text: string): number {
  const queue = getQueue(p.sessionId);
  const idx = p.index <= queue.length ? p.index : 0;
  const item: QueuedMessage = { id: p.id, text: text, createdAt: p.createdAt, status: 'pending' };
  queue.splice(idx, 0, item);
  persistQueue(p.sessionId, queue);
  return idx;
}

/** 保存编辑：把编辑后的文本按原位置（或队首）插回队列，清除编辑态与待恢复记录。 */
function saveQueuedEdit(text: string): void {
  const p = _editingPending;
  if (!p) return;
  _editingPending = null;
  _clearEditPendingStorage(p.sessionId);
  const idx = _reinsertQueued(p, text);
  if (currentSessionId === p.sessionId) {
    renderQueuePanel();
    updateQueueBadge();
    // 插回队首且 worker 空闲 → 立即发送（与 moveQueued 到队首的行为一致）
    if (idx === 0) flushQueue();
  }
}

/** 取消编辑：把原文按原位置插回队列（恢复原值，不丢消息），清除编辑态与待恢复记录。 */
function cancelQueuedEdit(): void {
  const p = _editingPending;
  if (!p) return;
  _editingPending = null;
  _clearEditPendingStorage(p.sessionId);
  _reinsertQueued(p, p.originalText);
  if (currentSessionId === p.sessionId) {
    renderQueuePanel();
    updateQueueBadge();
  }
}

/** 重排：↑(-1) / ↓(+1) 与相邻项 swap；提到队首且 worker 空闲 → 立即触发发送。 */
function moveQueued(id: string, delta: number): void {
  if (!currentSessionId) return;
  const sid = currentSessionId;
  const queue = getQueue(sid);
  const idx = queue.findIndex(function (x) { return x.id === id; });
  if (idx < 0) return;
  const target = idx + delta;
  if (target < 0 || target >= queue.length) return;
  const tmp = queue[idx];
  queue[idx] = queue[target];
  queue[target] = tmp;
  persistQueue(sid, queue);
  renderQueuePanel();
  updateQueueBadge();
  if (target === 0) flushQueue();
}

function clearQueue(): void {
  if (!currentSessionId) return;
  persistQueue(currentSessionId, []);
  renderQueuePanel();
  updateQueueBadge();
  toast('已清空发送队列');
}

/** 面板开关：class 控制显隐；打开时渲染 + 把滚动按钮上移避免遮挡。 */
function toggleQueuePanel(): void {
  const panel = document.getElementById('queuePanel');
  if (!panel) return;
  const btn = document.getElementById('queueToggleBtn');
  const open = panel.classList.toggle('open');
  if (btn) btn.classList.toggle('open', open);
  if (open) renderQueuePanel();
  const scrollBtn = document.getElementById('scrollBottomBtn');
  if (scrollBtn) {
    scrollBtn.style.bottom = open ? (78 + panel.offsetHeight) + 'px' : '70px';
  }
}

/** 更新 ^ 按钮角标：队列非空时显示数量 + 高亮。 */
function updateQueueBadge(): void {
  const btn = document.getElementById('queueToggleBtn');
  if (!btn) return;
  const queue = currentSessionId ? getQueue(currentSessionId) : [];
  btn.classList.toggle('has-queue', queue.length > 0);
  btn.title = queue.length > 0 ? '发送队列（' + queue.length + ' 条待发）' : '发送队列';
  const badge = document.getElementById('queueBadge');
  if (badge) {
    badge.textContent = String(queue.length);
    badge.hidden = queue.length === 0;
  }
}

/** 渲染面板行列表：单行截断 + title 全文 + hover 操作按钮（✎ ↑ ↓ 🗑）。 */
function renderQueuePanel(): void {
  const listEl = document.getElementById('queueList');
  if (!listEl) return;
  const countEl = document.getElementById('queueCount');
  const clearBtn = document.getElementById('queueClearBtn');
  const queue = currentSessionId ? getQueue(currentSessionId) : [];
  // 恢复当前 session 的批量拼接开关（session 切换 / 面板打开时）
  const batchCb = document.getElementById('queueBatchToggle') as HTMLInputElement | null;
  if (batchCb) batchCb.checked = currentSessionId ? isBatchEnabled(currentSessionId) : false;
  // 编辑中的出队项显示位置：原位置仍有效则插在原位置，否则放队首（与保存时插回策略一致）
  const editIdx = _editingPending && _editingPending.index <= queue.length ? _editingPending.index : 0;
  if (countEl) countEl.textContent = String(queue.length);
  if (clearBtn) clearBtn.style.display = queue.length > 0 ? '' : 'none';
  listEl.innerHTML = '';
  if (queue.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'queue-empty';
    empty.textContent = '队列为空';
    listEl.appendChild(empty);
    // 编辑中的出队项仍在编辑态：空队列下编辑行放最前（不占队列长度、不影响角标）
    _insertEditRow(listEl, editIdx);
    updateQueueBadge();
    return;
  }
  queue.forEach(function (item, index) {
    const row = document.createElement('div');
    row.className = 'queue-row';
    row.dataset.id = item.id;

    const text = document.createElement('span');
    text.className = 'queue-text';
    text.textContent = item.text;
    text.title = item.text; // hover 显示全文
    row.appendChild(text);

    const actions = document.createElement('span');
    actions.className = 'queue-actions';

    const btnEdit = document.createElement('button');
    btnEdit.type = 'button'; btnEdit.className = 'q-btn'; btnEdit.textContent = '\u270E'; btnEdit.title = '编辑';
    const btnUp = document.createElement('button');
    btnUp.type = 'button'; btnUp.className = 'q-btn'; btnUp.textContent = '\u2191'; btnUp.title = '上移';
    btnUp.disabled = index === 0;
    const btnDown = document.createElement('button');
    btnDown.type = 'button'; btnDown.className = 'q-btn'; btnDown.textContent = '\u2193'; btnDown.title = '下移';
    btnDown.disabled = index === queue.length - 1;
    const btnDel = document.createElement('button');
    btnDel.type = 'button'; btnDel.className = 'q-btn q-btn-danger'; btnDel.textContent = '\uD83D\uDDD1'; btnDel.title = '删除';

    btnEdit.addEventListener('click', function (e) { e.stopPropagation(); startEditQueued(item.id); });
    btnUp.addEventListener('click', function (e) { e.stopPropagation(); moveQueued(item.id, -1); });
    btnDown.addEventListener('click', function (e) { e.stopPropagation(); moveQueued(item.id, 1); });
    btnDel.addEventListener('click', function (e) { e.stopPropagation(); removeQueued(item.id); });

    actions.appendChild(btnEdit);
    actions.appendChild(btnUp);
    actions.appendChild(btnDown);
    actions.appendChild(btnDel);
    row.appendChild(actions);
    listEl.appendChild(row);
  });
  // 编辑中的出队项：按原位置插入显示
  _insertEditRow(listEl, editIdx);
  updateQueueBadge();
}

/** 创建编辑行（textarea + Enter 保存 / Esc 取消），插入 listEl 的 index 位置。
 *  renderQueuePanel 每次重绘时调用，保证编辑态在队列项变化（flush 成功、重排、
 *  清空等触发重绘）后仍保留；输入内容通过 _editingPending.draftText 同步，重绘不丢。 */
function _insertEditRow(listEl: HTMLElement, index: number): void {
  const p = _editingPending;
  if (!p || p.sessionId !== currentSessionId) return;
  const row = document.createElement('div');
  row.className = 'queue-row editing';
  row.dataset.id = p.id;
  const ta = document.createElement('textarea');
  ta.className = 'queue-edit';
  ta.value = p.draftText;
  ta.rows = 2;
  row.appendChild(ta);
  ta.addEventListener('input', function () {
    if (_editingPending) _editingPending.draftText = ta.value;
  });
  let handled = false;
  ta.addEventListener('keydown', function (e) {
    if (handled) return;
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handled = true;
      const newText = ta.value.trim();
      if (newText) saveQueuedEdit(newText);
      else cancelQueuedEdit(); // 清空内容 → 视为取消，恢复原文
    } else if (e.key === 'Escape') {
      e.preventDefault();
      handled = true;
      cancelQueuedEdit();
    }
  });
  const ref = listEl.children[index] || null;
  listEl.insertBefore(row, ref);
}

/** 进入行内编辑态：先把消息从队列出队（内存 _queueCache + persistQueue 落盘），
 *  记录原位置与原文，再把该行替换为 textarea。
 *  编辑期间消息不在队列，flushQueue()/getQueue() 都看不到它，不可能被自动发出；
 *  保存/取消时按原位置插回。 */
function startEditQueued(id: string): void {
  if (!currentSessionId) return;
  const sid = currentSessionId;
  // 同一时刻只允许一条消息处于编辑态：开始新编辑前，若已有编辑中的先按取消处理恢复
  if (_editingPending) cancelQueuedEdit();
  const queue = getQueue(sid);
  const idx = queue.findIndex(function (x) { return x.id === id; });
  if (idx < 0) return;
  const item = queue[idx];
  // 1) 出队：编辑期间该消息不在队列，自动发送（逐条/批量拼接）都不会碰到它
  queue.splice(idx, 1);
  persistQueue(sid, queue);
  // 2) 记录原位置 + 原文，并作为待恢复项落盘（刷新/切换 session 后插回，避免丢消息）
  _editingPending = {
    id: id,
    sessionId: sid,
    index: idx,
    originalText: item.text,
    draftText: item.text,
    createdAt: item.createdAt,
  };
  _persistEditPending(_editingPending);
  renderQueuePanel();
  updateQueueBadge();
  // 3) 聚焦重绘后插入的编辑行
  const listEl = document.getElementById('queueList');
  if (!listEl) return;
  const row = listEl.querySelector<HTMLElement>('.queue-row.editing');
  const ta = row ? row.querySelector<HTMLTextAreaElement>('textarea') : null;
  if (ta) {
    ta.focus();
    const end = ta.value.length;
    ta.setSelectionRange(end, end);
  }
}

/** 刷新 / 切换回 session 时恢复被中断的编辑：待恢复项（localStorage）若不在队列中
 *  则按原位置插回（原位置已失效则追加队尾），随后清除待恢复标记。
 *  保存/取消已正常完成的编辑没有待恢复项，此函数为 no-op。 */
function _restoreInterruptedEdit(sessionId: string): void {
  // 该 session 当前仍在编辑中（内存态存在）则不是"被中断"，无需恢复
  if (_editingPending && _editingPending.sessionId === sessionId) return;
  const p = _loadEditPending(sessionId);
  if (!p) return;
  _clearEditPendingStorage(sessionId);
  const queue = getQueue(sessionId);
  // 队列中已有同 id 项（保存成功过 / 已恢复过）则无需恢复
  if (queue.some(function (x) { return x.id === p.id; })) return;
  const item: QueuedMessage = { id: p.id, text: p.originalText, createdAt: p.createdAt, status: 'pending' };
  // 恢复时插入原位置；原位置已超出队列（前面的项被发走/删除）则追加队尾，
  // 避免插到队首打乱现有待发顺序。
  const idx = p.index <= queue.length ? p.index : queue.length;
  queue.splice(idx, 0, item);
  persistQueue(sessionId, queue);
}

/** 自动发送队列（逐条串行 / 批量拼接两种模式）：worker idle / offline（可 spawn）时发送，
 *  发送成功后出队（逐条 shift 队首；批量清空）并持久化；失败（WS closed / spawn 失败 / held）保留待重试。
 *  `_queueSendingId` 防同一条在异步窗口内被 idle 事件重复触发两次。 */
function flushQueue(): void {
  if (!currentSessionId) return;
  const sid = currentSessionId;
  const queue = getQueue(sid);
  if (queue.length === 0) return;
  if (_queueSendingId) return; // 已有 1 条在发送中，等下一次 idle 事件
  const s = modelData.find(function (x) { return x.id === sid; });
  const status = s ? (s.workerStatus || 'offline') : 'offline';
  if (status === 'held') return; // takeover：服务端硬拒，跳过自动发送
  if (status !== 'idle' && status !== 'offline') return; // queued/running/…：等 idle 事件

  // 批量拼接发送（勾选"拼接发送"时）：把队列中全部项的 text 用分隔符拼成一条发出。
  // 分隔符 `\n\n---\n\n` 用于明确区分各条原文；编辑中的消息已出队、不参与拼接。
  if (isBatchEnabled(sid)) {
    // 防御性过滤：编辑中的出队消息理论上不在队列里，双保险避免拼进标记为编辑中的项
    const editId = _editingPending ? _editingPending.id : null;
    const items = queue.filter(function (x) { return x.id !== editId; });
    if (items.length === 0) return; // 队列中无可发项（全部在编辑中），直接 return
    const joinedIds = new Set(items.map(function (x) { return x.id; }));
    const joined = items.map(function (x) { return x.text; }).join('\n\n---\n\n');
    _queueSendingId = _BATCH_SENDING_ID; // 拼接消息也算 1 条发送中，复用单飞锁
    _sendText(joined, function (ok) {
      _queueSendingId = null;
      if (!ok) return; // 发送失败：保留队列待下次重试
      if (currentSessionId === sid) addMessage('user', joined);
      const q = getQueue(sid);
      // 只清掉本次拼接发出的项：编辑中的出队项不在队列里（在 _editingPending + 待恢复记录），
      // 拼接发送在途期间新入队的项（含保存/取消后插回的编辑项）也保留，避免误清。
      const remain = q.filter(function (x) { return !joinedIds.has(x.id); });
      persistQueue(sid, remain);
      if (currentSessionId === sid) renderQueuePanel();
      updateQueueBadge();
    });
    return;
  }

  const head = queue[0];
  _queueSendingId = head.id;
  _sendText(head.text, function (ok) {
    _queueSendingId = null;
    if (!ok) return; // 发送失败：保留队首待下次重试
    if (currentSessionId === sid) addMessage('user', head.text);
    const q = getQueue(sid);
    // 按 id 精确删除已发送项：即使发送在途期间用户重排/删除了其它项也不误删
    const sentIdx = q.findIndex(function (x) { return x.id === head.id; });
    if (sentIdx >= 0) {
      q.splice(sentIdx, 1);
      persistQueue(sid, q);
    }
    if (currentSessionId === sid) renderQueuePanel();
    updateQueueBadge();
  });
}

// ── Send message ──

/** 发送单条文本：封装原有 spawn/settings/doSend 链路，send() 与 flush 共用。
 *  消息真正发出（WS 已投递）后回调 onSent(true)；失败（WS closed / spawn / settings）回调 onSent(false)。
 *  注意：此函数不清理输入框，避免 flush 时误清用户正在输入的草稿。 */
function _sendText(text: string, onSent: (ok: boolean) => void): void {
  const sid = currentSessionId;

  function doSend(): void {
    const msg = JSON.stringify({
      type: 'user_inject',
      sessionId: sid,
      text: text,
    });
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(msg);
      onSent(true);
      return;
    }
    if (ws.readyState === WebSocket.CONNECTING) {
      // Wait for connection to open (common on slow mobile networks)
      ws.addEventListener('open', function handler() {
        ws.removeEventListener('open', handler);
        ws.send(msg);
        onSent(true);
      }, { once: true } as any);
      return;
    }
    // CLOSED or CLOSING — give up
    toast('Connection lost. Please refresh the page.');
    onSent(false);
  }

  if (!currentWorkerId) {
    const body: Record<string, unknown> = {
      sessionId: sid,
    };
    if (hasPendingChanges()) {
      Object.assign(body, _buildSettingsBody());
    }
    fetch('/api/spawn', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then((r: Response) => r.json())
      .then((d: ApiGenericResponse) => {
        if (d.error) {
          toast('Spawn failed: ' + d.error);
          onSent(false);
          return;
        }
        currentWorkerId = d.workerId ?? null;
        doSend();
      })
      .catch(function () {
        toast('Spawn failed: network error');
        onSent(false);
      });
    return;
  }

  // worker exists: if panel has unapplied changes, apply them first, then send
  if (hasPendingChanges()) {
    fetch('/api/worker/' + currentWorkerId + '/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_buildSettingsBody()),
    })
      .then((r: Response) => r.json())
      .then((d: ApiGenericResponse) => {
        if (d.error) { toast(d.error); onSent(false); return; }
        markSettingsApplied();
        doSend();
      })
      .catch(function () {
        toast('Failed to apply settings: network error');
        onSent(false);
      });
    return;
  }

  doSend();
}

function send(): void {
  const input = document.getElementById('chatInput') as HTMLInputElement;
  const text = input.value.trim();
  if (!currentSessionId) {
    toast('Select a session first');
    return;
  }
  if (!text) return;
  const s = modelData.find((x: Session) => x.id === currentSessionId);
  if (s && (s.workerStatus === 'running' || s.workerStatus === 'held')) {
    // worker 忙：不再拒绝，改为入队（空闲后自动逐条发送）
    enqueueMessage(text);
    return;
  }
  input.value = '';
  _inputDrafts.delete(currentSessionId);
  const sid = currentSessionId;
  _sendText(text, function (ok) {
    if (ok && currentSessionId === sid) addMessage('user', text);
  });
}

// ── New Session (modal) ──

function _doCreateSession(name: string, workdir: string | null, adapter?: string): void {
  // Optimistic UI: placeholder immediately
  const adp = adapter || currentAdapter || 'cbc';
  const placeholder: Session = {
    id: '__pending_' + name,
    name: '...',
    adapter: adp,
    model: defaultModel(),
    history: [],
    alwaysThinkingEnabled: false,
    effort: '',
  };
  modelData.push(placeholder);
  selectSession(placeholder.id);
  const body: Record<string, string> = { name: name, adapter: adp };
  if (workdir) body.workdir = workdir;
  const profileSel = document.getElementById('nsProfileSelect') as HTMLSelectElement;
  const sessionTemplate = profileSel ? profileSel.value : '';
  if (sessionTemplate) body.sessionTemplate = sessionTemplate;
  fetch('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
    .then((r: Response) => r.json())
    .then((d: Session & ApiGenericResponse) => {
      if (d.error) {
        toast(d.error);
        refreshSessions();
        return;
      }
      // Replace placeholder with real data
      for (let i = 0; i < modelData.length; i++) {
        if (modelData[i].id === placeholder.id) {
          modelData[i] = d as Session;
          break;
        }
      }
      if (currentSessionId === placeholder.id) {
        currentSessionId = d.id;
        updateTopBar();
      }
      renderSessionList();
    });
}

function quickNewSession(): void {
  let name = 'session-' + (modelData.length + 1);
  let n = 1;
  while (modelData.find((s: Session) => s.name === name)) {
    name = 'session-' + (modelData.length + n);
    n++;
  }
  _doCreateSession(name, null, currentAdapter);
}

function newSession(): void {
  const modal = document.getElementById('newSessionModal') as HTMLElement;
  const nameInput = document.getElementById('nsNameInput') as HTMLInputElement;
  const workdirInput = document.getElementById('nsWorkdirInput') as HTMLInputElement;
  nameInput.value = '';
  workdirInput.value = '';
  _populateNewSessionAdapterSelect();
  _populateNewSessionProfileSelect();
  modal.classList.add('open');
  nameInput.focus();
}

function deleteSession(id: string): void {
  if (id.indexOf('__pending_') === 0) {
    toast('Wait for session to be created first');
    return;
  }
  if (!confirm('Delete session ' + id.slice(0, 12) + '\u2026?')) return;
  // Optimistic UI: remove immediately, recover on failure
  modelData = modelData.filter(function (s) { return s.id !== id; });
  _removeQueueStorage(id);
  if (currentSessionId === id) {
    currentSessionId = null;
    currentWorkerId = null;
    showEmpty();
  }
  renderSessionList();
  fetch('/api/sessions/' + id, { method: 'DELETE' })
    .then((r: Response) => r.json())
    .then((d: ApiGenericResponse) => {
      if (d.error) {
        toast(d.error);
        return;
      }
      if (currentSessionId === id) {
        currentSessionId = null;
        currentWorkerId = null;
        showEmpty();
      }
      refreshSessions();
    });
}

// ── Multi-select mode ──

function enterMultiSelect(initId: string): void {
  _multiSelectMode = true;
  _selectedIds = new Set([initId]);
  renderSessionList();
  showMultiSelectBar();
}

function exitMultiSelect(): void {
  _multiSelectMode = false;
  _selectedIds.clear();
  renderSessionList();
  hideMultiSelectBar();
}

function toggleMultiSelect(id: string, checked: boolean): void {
  if (checked) _selectedIds.add(id);
  else _selectedIds.delete(id);
  updateSelectedCount();
  var el = document.querySelector('.sess-item[data-session-id="' + id + '"]');
  if (el) {
    if (checked) el.classList.add('selected');
    else el.classList.remove('selected');
  }
}

function batchDeleteSelected(): void {
  var ids = Array.from(_selectedIds);
  if (ids.length === 0) return;
  if (!confirm('Delete ' + ids.length + ' selected session(s)?')) return;

  modelData = modelData.filter(function (s: Session) { return !_selectedIds.has(s.id); });
  _selectedIds.forEach(function (id) { _removeQueueStorage(id); });
  ids.forEach(function (id) {
    if (currentSessionId === id) { currentSessionId = null; currentWorkerId = null; showEmpty(); }
  });

  _multiSelectMode = false;
  _selectedIds.clear();
  renderSessionList();
  hideMultiSelectBar();

  fetch('/api/sessions/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionIds: ids }),
  })
    .then(function (r: Response) { return r.json(); })
    .then(function (d: ApiGenericResponse) {
      if (d.error) { toast(d.error); return; }
      refreshSessions();
    });
}

function showMultiSelectBar(): void {
  var bar = document.getElementById('multiSelectBar')!;
  bar.style.display = 'flex';
  updateSelectedCount();
}

function hideMultiSelectBar(): void {
  var bar = document.getElementById('multiSelectBar');
  if (bar) bar.style.display = 'none';
}

function updateSelectedCount(): void {
  var el = document.getElementById('selectedCount');
  if (el) el.textContent = String(_selectedIds.size);
}

// ── Session gear menu ──

function closeSessMenu(): void {
  const m = document.getElementById('sessMenu');
  if (m) m.remove();
}

function toggleSessMenu(e: MouseEvent, id: string): void {
  e.stopPropagation();
  e.preventDefault();
  const existing = document.getElementById('sessMenu');
  if (existing) { existing.remove(); return; }

  const s = modelData.find((x: Session) => x.id === id);
  if (!s) return;

  const menu = document.createElement('div');
  menu.className = 'sess-menu';
  menu.id = 'sessMenu';
  menu.innerHTML =
    '<div class="sess-menu-item" onclick="closeSessMenu();renameSession(\'' + id + '\')">\u270E Rename</div>' +
    (s.cliSessionId
      ? '<div class="sess-menu-item" onclick="closeSessMenu();reimportSession(\'' + id + '\')">\u21BB Reimport</div>' +
        '<div class="sess-menu-item" onclick="closeSessMenu();branchSession(\'' + id + '\')">\u2442 Branch</div>'
      : '') +
    '<div class="sess-menu-item" onclick="closeSessMenu();openManageModal(\'' + id + '\')">\u2699 Manage</div>' +
    '<div class="sess-menu-item" onclick="closeSessMenu();openPostboxModal(\'' + id + '\')">\u2709 Postbox</div>' +
    '<div class="sess-menu-item" onclick="closeSessMenu();enterMultiSelect(\'' + id + '\')">\u2611 Select</div>' +
    '<div class="sess-menu-item sess-menu-danger" onclick="closeSessMenu();deleteSession(\'' + id + '\')">\u2715 Delete</div>';

  const btn = e.currentTarget as HTMLElement;
  btn.parentElement!.appendChild(menu);

  setTimeout(() => document.addEventListener('click', closeSessMenu, { once: true }), 0);
}

function renameSession(id: string): void {
  const newName = (prompt('New session name:') || '').trim();
  if (!newName) return;
  fetch('/api/sessions/' + id + '/rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: newName }),
  })
    .then((r: Response) => r.json())
    .then((d: ApiGenericResponse & { status?: string }) => {
      if (d.error) { toast(d.error); return; }
      refreshSessions();
    });
}

function reimportSession(id: string): void {
  const s = modelData.find((x: Session) => x.id === id);
  if (!s || !s.cliSessionId) return;
  const cwd = s.workdir || '';
  const body: Record<string, string> = { session_id: s.cliSessionId };
  if (cwd) body.cwd = cwd;
  const url = s.adapter === 'kimi' ? '/api/kimi/sessions/import' : '/api/cbc/sessions/import';
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
    .then((r: Response) => r.json())
    .then((d: Session & ApiGenericResponse) => {
      if (d.error) { toast(d.error); return; }
      // Replace old session with new in modelData
      for (let i = 0; i < modelData.length; i++) {
        if (modelData[i].id === id) {
          modelData[i] = d as Session;
          break;
        }
      }
      if (currentSessionId === id) {
        currentSessionId = d.id;
        updateTopBar();
        renderMessages(d.history || []);
      }
      renderSessionList();
      toast('Session reimported.');
    });
}

function branchSession(id: string): void {
  const s = modelData.find((x: Session) => x.id === id);
  if (!s || !s.cliSessionId) return;
  const defaultName = s.name? s.name + '-branch' : '';
  const newName = (prompt('Branch session name:', defaultName) || '').trim();
  if (!newName) return;
  fetch('/api/sessions/' + id + '/branch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: newName }),
  })
    .then((r: Response) => r.json())
    .then((d: Session & ApiGenericResponse) => {
      if (d.error) { toast(d.error); return; }
      refreshSessions();
    });
}

// ── Manage (pan_session managed relations) modal ──

function openManageModal(id: string): void {
  _manageSessionId = id;
  _manageFilter = '';
  _manageShowAll = false;
  const search = document.getElementById('manageSearch') as HTMLInputElement;
  if (search) search.value = '';
  const heading = document.getElementById('manageHeading');
  const s = modelData.find((x: Session) => x.id === id);
  if (heading) heading.textContent = s ? 'Manage: ' + s.name : 'Manage';
  renderManageList();
  (document.getElementById('manageModal') as HTMLElement).classList.add('open');
}

function manageFilter(value: string): void {
  _manageFilter = value;
  renderManageList();
}

function manageShowAll(): void {
  _manageShowAll = true;
  renderManageList();
}

function renderManageList(): void {
  const list = document.getElementById('manageList');
  const showAllBtn = document.getElementById('manageShowAll') as HTMLElement;
  if (!list) return;
  const s = modelData.find((x: Session) => x.id === _manageSessionId);
  if (!s) {
    list.innerHTML = '<div class="manage-empty">Session not found.</div>';
    if (showAllBtn) showAllBtn.style.display = 'none';
    return;
  }
  const managedSet = new Set(s.managed || []);
  const filter = _manageFilter.toLowerCase();
  const candidates = modelData.filter(function (c: Session) {
    if (c.id === s.id) return false;
    if (c.id.indexOf('__pending_') === 0) return false;
    if (!filter) return true;
    return c.name.toLowerCase().indexOf(filter) >= 0 || c.id.toLowerCase().indexOf(filter) >= 0;
  });
  // Managed sessions first, then alphabetically by name
  candidates.sort(function (a: Session, b: Session) {
    const am = managedSet.has(a.id) ? 0 : 1;
    const bm = managedSet.has(b.id) ? 0 : 1;
    if (am !== bm) return am - bm;
    return a.name.localeCompare(b.name);
  });
  const limit = _manageShowAll ? candidates.length : 20;
  const shown = candidates.slice(0, limit);
  if (showAllBtn) showAllBtn.style.display = (candidates.length > 20 && !_manageShowAll) ? '' : 'none';
  if (shown.length === 0) {
    list.innerHTML = '<div class="manage-empty">No candidate sessions.</div>';
    return;
  }
  let html = '';
  shown.forEach(function (c: Session) {
    const checked = managedSet.has(c.id);
    html += '<div class="manage-item">' +
      '<label class="manage-label">' +
      '<input type="checkbox"' + (checked ? ' checked' : '') +
      ' onchange="toggleManaged(\'' + c.id + '\',this.checked)">' +
      '<span class="manage-item-main">' + esc(c.name) + '</span>' +
      '<span class="manage-item-sub">' + esc(c.id) + '</span>' +
      '</label></div>';
  });
  list.innerHTML = html;
}

function toggleManaged(targetId: string, checked: boolean): void {
  const url = checked ? '/api/claim' : '/api/unclaim';
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ managerId: _manageSessionId, sessionId: targetId }),
  })
    .then((r: Response) => r.json())
    .then((d: ApiOpResponse) => {
      if (d.ok === false || d.error) {
        const msg = d.error && typeof d.error === 'object'
          ? (d.error.message || 'Operation failed')
          : (typeof d.error === 'string' ? d.error : 'Operation failed');
        toast(msg);
        refreshSessions();
        renderManageList();
        return;
      }
      // Optimistic local update so the list reflects the change immediately
      const s = modelData.find((x: Session) => x.id === _manageSessionId);
      if (s) {
        const arr = s.managed ? s.managed.slice() : [];
        const idx = arr.indexOf(targetId);
        if (checked && idx < 0) arr.push(targetId);
        if (!checked && idx >= 0) arr.splice(idx, 1);
        s.managed = arr;
      }
      toast(checked ? 'Claimed session' : 'Unclaimed session');
      renderManageList();
      refreshSessions();
    })
    .catch(function () {
      toast('Request failed');
      refreshSessions();
      renderManageList();
    });
}

// ── Postbox (QQ inbox subscriptions) modal ──

function openPostboxModal(id: string): void {
  _postboxSessionId = id;
  _postboxFilter = '';
  _postboxShowAll = false;
  _postboxContacts = null;
  _postboxError = '';
  const search = document.getElementById('postboxSearch') as HTMLInputElement;
  if (search) search.value = '';
  const heading = document.getElementById('postboxHeading');
  const s = modelData.find((x: Session) => x.id === id);
  if (heading) heading.textContent = s ? 'Postbox: ' + s.name : 'Postbox';
  renderPostboxList();
  (document.getElementById('postboxModal') as HTMLElement).classList.add('open');
  fetch('/api/qq/contacts')
    .then((r: Response) => r.json())
    .then((d: ApiQqContactsResponse) => {
      if (d.ok === false || d.error) {
        _postboxError = d.error && typeof d.error === 'object'
          ? (d.error.message || 'QQ plugin not connected')
          : (typeof d.error === 'string' ? d.error : 'QQ plugin not connected');
        _postboxContacts = null;
        renderPostboxList();
        return;
      }
      _postboxContacts = d.contacts || [];
      _postboxError = '';
      renderPostboxList();
    })
    .catch(function () {
      _postboxError = 'Failed to load QQ contacts';
      _postboxContacts = null;
      renderPostboxList();
    });
}

function postboxFilter(value: string): void {
  _postboxFilter = value;
  renderPostboxList();
}

function postboxShowAll(): void {
  _postboxShowAll = true;
  renderPostboxList();
}

function renderPostboxList(): void {
  const list = document.getElementById('postboxList');
  const showAllBtn = document.getElementById('postboxShowAll') as HTMLElement;
  if (!list) return;
  if (_postboxContacts === null) {
    list.innerHTML = _postboxError
      ? '<div class="manage-empty">' + esc(_postboxError) + '</div>'
      : '<div class="manage-empty">Loading\u2026</div>';
    if (showAllBtn) showAllBtn.style.display = 'none';
    return;
  }
  const s = modelData.find((x: Session) => x.id === _postboxSessionId);
  const subs = new Set(s ? s.qqSubscriptions || [] : []);
  const filter = _postboxFilter.toLowerCase();
  const candidates = _postboxContacts.filter(function (c: QqContact) {
    if (!filter) return true;
    return (c.peerName || '').toLowerCase().indexOf(filter) >= 0 ||
      String(c.peerUin || '').toLowerCase().indexOf(filter) >= 0;
  });
  const limit = _postboxShowAll ? candidates.length : 20;
  const shown = candidates.slice(0, limit);
  if (showAllBtn) showAllBtn.style.display = (candidates.length > 20 && !_postboxShowAll) ? '' : 'none';
  if (shown.length === 0) {
    list.innerHTML = '<div class="manage-empty">No contacts.</div>';
    return;
  }
  let html = '';
  shown.forEach(function (c: QqContact) {
    const targetType = c.chatType === 2 ? 'group' : 'user';
    const key = targetType + ':' + c.peerUin;
    const checked = subs.has(key);
    const typeLabel = c.chatType === 2 ? 'group' : 'user';
    html += '<div class="manage-item">' +
      '<label class="manage-label">' +
      '<input type="checkbox"' + (checked ? ' checked' : '') +
      ' onchange="toggleQqSubscription(\'' + c.peerUin + '\',' + c.chatType + ',this.checked)">' +
      '<span class="manage-item-main">' + esc(c.peerName || String(c.peerUin)) + '</span>' +
      '<span class="manage-item-sub">' + esc(String(c.peerUin) + ' \u00b7 ' + typeLabel) + '</span>' +
      '</label></div>';
  });
  list.innerHTML = html;
}

function toggleQqSubscription(peerUin: string, chatType: number, checked: boolean): void {
  const targetType = chatType === 2 ? 'group' : 'user';
  const url = checked ? '/api/qq/subscribe' : '/api/qq/unsubscribe';
  fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionId: _postboxSessionId, target_type: targetType, target_id: peerUin }),
  })
    .then((r: Response) => r.json())
    .then((d: ApiOpResponse) => {
      if (d.error) {
        toast(typeof d.error === 'string' ? d.error : (d.error.message || 'Operation failed'));
        refreshSessions();
        renderPostboxList();
        return;
      }
      // Optimistic local update so the list reflects the change immediately
      const s = modelData.find((x: Session) => x.id === _postboxSessionId);
      if (s) {
        const key = targetType + ':' + peerUin;
        const arr = s.qqSubscriptions ? s.qqSubscriptions.slice() : [];
        const idx = arr.indexOf(key);
        if (checked && idx < 0) arr.push(key);
        if (!checked && idx >= 0) arr.splice(idx, 1);
        s.qqSubscriptions = arr;
      }
      toast(checked ? 'Subscribed' : 'Unsubscribed');
      renderPostboxList();
      refreshSessions();
    })
    .catch(function () {
      toast('Request failed');
      refreshSessions();
      renderPostboxList();
    });
}

// ── Adapter config loading ──

/** Load adapter configuration for a specific adapter name, populate selects. */
function loadAdapterConfig(adapterName: string): void {
  fetch('/api/adapter/config?adapter=' + encodeURIComponent(adapterName))
    .then((r: Response) => r.json())
    .then((data: ApiConfigResponse) => {
      const cfg: AdapterConfig = {
        models: data.models || [],
        defaultModel: data.defaultModel || 'deepseek-v4-flash',
        effortValues: data.effortValues || [],
        permissionModes: data.permissionModes || [],
        defaultPermissionMode: data.defaultPermissionMode || '',
        supportedSettings: data.supportedSettings || ['model', 'permissionMode', 'thinking', 'effort'],
      };
      adapterConfigs.set(adapterName, cfg);
      currentAdapter = adapterName;
      _adapterConfigReady = true;
      buildModelSelect();
      buildModeSelect();
      buildEffortSelect();
      updateSettingsVisibility();
      if (document.getElementById('settingsPanel')!.classList.contains('open'))
        syncPanelFromServer();
    })
    .catch(function () {
      // Server unavailable — will retry on settings panel open
    });
}

/** Show/hide settings fields based on supportedSettings of the current adapter. */
function updateSettingsVisibility(): void {
  const modeGroup = document.getElementById('modeGroup') as HTMLElement;
  const thinkingGroup = document.getElementById('thinkingGroup') as HTMLElement;
  const effortGroup = document.getElementById('effortGroup') as HTMLElement;
  if (modeGroup) modeGroup.style.display = supportsSetting('permissionMode') ? '' : 'none';
  if (thinkingGroup) thinkingGroup.style.display = supportsSetting('thinking') ? '' : 'none';
  // Effort only visible when BOTH thinking and effort are supported
  if (effortGroup) effortGroup.style.display = (supportsSetting('thinking') && supportsSetting('effort')) ? '' : 'none';
}

/** Populate the Agent CLI selector in the new-session modal. */
function _populateNewSessionAdapterSelect(): void {
  const sel = document.getElementById('nsAdapterSelect') as HTMLSelectElement;
  if (!sel) return;
  sel.innerHTML = '';
  availableAdapters.forEach((a: string) => {
    const opt = document.createElement('option');
    opt.value = a;
    opt.textContent = adapterLabel(a);
    sel.appendChild(opt);
  });
  sel.value = currentAdapter || 'cbc';
}

/** Fetch session templates (GET /api/session-templates). Resolves to the
 *  template array; resolves to [] on failure so callers stay simple. */
function _fetchSessionTemplates(): Promise<SessionTemplate[]> {
  return fetch('/api/session-templates')
    .then((r: Response) => r.json())
    .then((data: ApiSessionTemplatesResponse) => data.sessionTemplates || [])
    .catch(function () { return []; });
}

/** Populate the session-template selector in the new-session modal. First
 *  option is "无 session template" (value ""), then one option per template labelled
 *  "name (model) [MCP] (manifest.json)" ([MCP] when the template ships mcpServers;
 *  the trailing "manifest.json" pinpoints which manifest defined it). */
function _populateNewSessionProfileSelect(): void {
  const sel = document.getElementById('nsProfileSelect') as HTMLSelectElement;
  if (!sel) return;
  sel.innerHTML = '<option value="">无 session template</option>';
  _fetchSessionTemplates().then((templates: SessionTemplate[]) => {
    templates.forEach((p: SessionTemplate) => {
      const opt = document.createElement('option');
      opt.value = p.name;
      let label = p.name + ' (' + (p.model || '?') + ')';
      if (p.mcpServers && p.mcpServers.length > 0) label += ' [MCP]';
      label += ' (' + _manifestLabel(p) + ')';
      opt.textContent = label;
      sel.appendChild(opt);
    });
  });
}

/** Readable manifest location for a template: prefer the backend-computed
 *  short label (e.g. "packages/mcp/manifest.json"); fall back to the last
 *  directory of the full path + "/manifest.json" when the label is missing. */
function _manifestLabel(p: SessionTemplate): string {
  if (p.sourceManifestLabel) return p.sourceManifestLabel;
  if (p.sourceManifest) {
    const parts = p.sourceManifest.replace(/\\/g, '/').split('/').filter(Boolean);
    return (parts[parts.length - 1] || '') + '/manifest.json';
  }
  return 'manifest.json';
}

/** Load the list of adapters and populate the new-session select. */
async function _loadAdapterListAndConfig(adapter: string): Promise<void> {
  if (availableAdapters.length === 0) {
    try {
      const r = await fetch('/api/adapters');
      const d = await r.json();
      const adaptersObj = d.adapters || [{name: 'cbc'}];
      availableAdapters = adaptersObj.map((a: any) => a.name || a);
    } catch (e) {
      availableAdapters = ['cbc'];
    }
  }
  _populateNewSessionAdapterSelect();
}

// ── Init ──

function init(): void {
  // Load adapter list, then load default config serially
  _loadAdapterListAndConfig('cbc')
    .then(() => {
      loadAdapterConfig('cbc');
    });

  refreshSessions();

  // 发送队列面板初始状态（默认折叠）
  renderQueuePanel();
  updateQueueBadge();

  // 点击编辑框外 → 取消编辑（恢复原文）。
  // 面板内交互控件（清空 / ↑↓ / 🗑 / ✎ / 拼接发送勾选）的点击由各自的 handler 处理，
  // 不在此取消：保证「编辑中清空」只作用于已入队项，编辑中的出队项保持出队直到保存/取消。
  document.addEventListener('click', function (e) {
    if (!_editingPending) return;
    const t = e.target as HTMLElement | null;
    if (!t || !t.closest) return;
    if (t.closest('#queuePanel button') || t.closest('.queue-batch-label')) return; // 面板控件：交给各自的 handler
    const listEl = document.getElementById('queueList');
    const row = listEl ? listEl.querySelector('.queue-row.editing') : null;
    if (row && row.contains(t)) return; // 点在编辑框内：不取消
    cancelQueuedEdit();
  });

  // Lazy-load older messages on scroll (throttled; skip during render/load).
  let _scrollTimer: ReturnType<typeof setTimeout> | null = null;
  document.getElementById('messages')!.addEventListener('scroll', function () {
    updateScrollToBottomBtn();
    if (_rendering || _historyLoading) return;
    if (_scrollTimer !== null) return;
    _scrollTimer = setTimeout(() => {
      _scrollTimer = null;
      const el = document.getElementById('messages') as HTMLElement;
      if (el.scrollTop <= 200) {
        loadOlderMessages();
      }
    }, 150);
  });

  // Scroll-to-bottom button
  const scrollToBottomBtn = document.getElementById('scrollBottomBtn');
  if (scrollToBottomBtn) {
    scrollToBottomBtn.addEventListener('click', () => {
      scrollToBottom();
    });
  }

  // ── Import Modal ──
  const importBtn = document.getElementById('importBtn') as HTMLButtonElement;
  const importDropdown = document.getElementById('importDropdown') as HTMLDivElement;
  const importModal = document.getElementById('importModal') as HTMLDivElement;
  const closeImportModal = document.getElementById('closeImportModal') as HTMLButtonElement;
  const importAdapterSelect = document.getElementById('importAdapterSelect') as HTMLSelectElement;
  const cbcImportFilters = document.getElementById('cbcImportFilters') as HTMLDivElement;
  const kimiImportFilters = document.getElementById('kimiImportFilters') as HTMLDivElement;
  const cbcDriveSelect = document.getElementById('cbcDriveSelect') as HTMLSelectElement;
  const cbcProjectSelect = document.getElementById('cbcProjectSelect') as HTMLSelectElement;
  const kimiWorkspaceSelect = document.getElementById('kimiWorkspaceSelect') as HTMLSelectElement;
  const importSessionListEl = document.getElementById('importSessionList') as HTMLDivElement;
  const importSessionCountEl = document.getElementById('importSessionCount') as HTMLDivElement;

  let allProjects: CbcProject[] = [];
  let currentProjectDir = '';

  // Import dropdown toggle
  (window as any).openImport = function (adapter: string): void {
    importDropdown.classList.remove('open');
    importAdapterSelect.value = adapter;
    importAdapterSelect.dispatchEvent(new Event('change'));
    importModal.classList.add('open');
  };

  importBtn.addEventListener('click', (e: MouseEvent) => {
    e.stopPropagation();
    importDropdown.classList.toggle('open');
  });
  document.addEventListener('click', (e: MouseEvent) => {
    if (!importDropdown.contains(e.target as Node) && e.target !== importBtn) {
      importDropdown.classList.remove('open');
    }
  });

  importAdapterSelect.addEventListener('change', () => {
    switchImportAdapter(importAdapterSelect.value || 'cbc');
  });

  let allKimiWorkspaces: KimiWorkspace[] = [];
  let currentKimiCwd = '';

  function switchImportAdapter(adapter: string): void {
    const isCbc = adapter === 'cbc';
    cbcImportFilters.style.display = isCbc ? '' : 'none';
    kimiImportFilters.style.display = isCbc ? 'none' : '';
    importSessionListEl.innerHTML = '<div class="im-loading">Loading\u2026</div>';
    importSessionCountEl.textContent = '';

    if (isCbc) {
      cbcDriveSelect.innerHTML = '<option value="">Loading...</option>';
      cbcProjectSelect.innerHTML = '<option value="">-</option>';
      fetchCbcProjects()
        .then((projects) => {
          allProjects = projects;
          if (allProjects.length === 0) {
            cbcDriveSelect.innerHTML = '<option value="">No projects</option>';
            importSessionListEl.innerHTML = '<div class="im-loading">No cbc projects found.</div>';
            return;
          }
          buildDriveSelect();
        })
        .catch((e: any) => {
          cbcDriveSelect.innerHTML = '<option value="">Failed</option>';
          importSessionListEl.innerHTML = `<div class="im-loading" style="color:#f85149">Error: ${esc(e.message)}</div>`;
        });
    } else {
      kimiWorkspaceSelect.innerHTML = '<option value="">Loading...</option>';
      fetchKimiWorkspaces()
        .then((workspaces: KimiWorkspace[]) => {
          allKimiWorkspaces = workspaces;
          if (allKimiWorkspaces.length === 0) {
            kimiWorkspaceSelect.innerHTML = '<option value="">No workspaces</option>';
            importSessionListEl.innerHTML = '<div class="im-loading">No Kimi workspaces found.</div>';
            return;
          }
          buildKimiWorkspaceSelect();
        })
        .catch((e: any) => {
          kimiWorkspaceSelect.innerHTML = '<option value="">Failed</option>';
          importSessionListEl.innerHTML = `<div class="im-loading" style="color:#f85149">Error: ${esc(e.message)}</div>`;
        });
    }
  }

  function buildDriveSelect(): void {
    const drives = [...new Set(allProjects.map((p: CbcProject) => p.drive))].sort();
    cbcDriveSelect.innerHTML = '<option value="">Drive</option>';
    drives.forEach((d: string) => {
      const total = allProjects.filter((p: CbcProject) => p.drive === d)
        .reduce((sum: number, p: CbcProject) => sum + (p.resumable_count || p.session_count), 0);
      const opt = document.createElement('option');
      opt.value = d;
      opt.textContent = `${d} (${total} sessions)`;
      cbcDriveSelect.appendChild(opt);
    });
    if (drives.length === 1) {
      cbcDriveSelect.value = drives[0];
      cbcDriveSelect.dispatchEvent(new Event('change'));
    }
  }

  function buildProjectSelect(drive: string): void {
    const projects = allProjects.filter((p: CbcProject) => p.drive === drive);
    projects.sort((a: CbcProject, b: CbcProject) => a.short_label.localeCompare(b.short_label));
    cbcProjectSelect.innerHTML = '<option value="">Project</option>';
    projects.forEach((p: CbcProject) => {
      const rCount = p.resumable_count || p.session_count;
      const opt = document.createElement('option');
      opt.value = p.project_dir;
      opt.textContent = `${p.short_label} (${rCount})`;
      cbcProjectSelect.appendChild(opt);
    });
    if (projects.length > 0) {
      cbcProjectSelect.value = projects[0].project_dir;
      currentProjectDir = projects[0].project_dir;
    }
  }

  function renderCbcSessions(sessions: CbcSessionItem[]): void {
    importSessionCountEl.textContent = sessions.length? `${sessions.length} session(s) found` : '';
    importSessionListEl.innerHTML = sessions.map((s: CbcSessionItem) => {
      const ts = s.last_timestamp? new Date(s.last_timestamp).toLocaleString() : '';
      const forkBadge = s.forked_from? ' \uD83D\uDD00' : '';
      return `<div class="im-item" data-adapter="cbc" data-sid="${esc(s.session_id)}" data-pd="${esc(s.project_dir)}">
        <div class="im-title">${esc(s.title || 'Untitled')}${forkBadge}</div>
        <div class="im-meta">${s.message_count} msgs \u00B7 ${esc(s.model || '?')} \u00B7 ${esc(ts)}</div>
      </div>`;
    }).join('');
    attachImportItemHandlers();
  }

  function buildKimiWorkspaceSelect(): void {
    kimiWorkspaceSelect.innerHTML = '<option value="">Workspace</option>';
    allKimiWorkspaces.forEach((w: KimiWorkspace) => {
      const opt = document.createElement('option');
      opt.value = w.root;
      opt.textContent = `${w.name || w.workspace_id} (${w.session_count})`;
      kimiWorkspaceSelect.appendChild(opt);
    });
    if (allKimiWorkspaces.length > 0) {
      kimiWorkspaceSelect.value = allKimiWorkspaces[0].root;
      currentKimiCwd = allKimiWorkspaces[0].root;
      fetchKimiSessions(currentKimiCwd)
        .then((sessions: KimiSessionItem[]) => renderKimiSessions(sessions))
        .catch((e: Error) => { importSessionListEl.innerHTML = `<div class="im-loading" style="color:#f85149">${esc(e.message)}</div>`; });
    }
  }

  function renderKimiSessions(sessions: KimiSessionItem[]): void {
    importSessionCountEl.textContent = sessions.length? `${sessions.length} session(s) found` : '';
    importSessionListEl.innerHTML = sessions.map((s: KimiSessionItem) => {
      const ts = s.updatedAt? new Date(s.updatedAt).toLocaleString() : '';
      return `<div class="im-item" data-adapter="kimi" data-sid="${esc(s.session_id)}" data-cwd="${esc(s.workDir)}">
        <div class="im-title">${esc(s.title || 'Untitled')}</div>
        <div class="im-meta">${s.message_count} msgs \u00B7 ${esc(s.model || '?')} \u00B7 ${esc(ts)}</div>
      </div>`;
    }).join('');
    attachImportItemHandlers();
  }

  function attachImportItemHandlers(): void {
    importSessionListEl.querySelectorAll<HTMLElement>('.im-item').forEach((el) => {
      el.addEventListener('click', async () => {
        const adapter = el.dataset['adapter'] || 'cbc';
        const sid = el.dataset['sid']!;
        el.style.opacity = '0.5';
        el.style.pointerEvents = 'none';
        let result: any;
        if (adapter === 'kimi') {
          const cwd = el.dataset['cwd'] || '';
          result = await importKimiSession(sid, cwd);
        } else {
          const pd = el.dataset['pd'] || '';
          result = await importCbcSession(sid, pd);
        }
        if (result.error) {
          toast(result.error);
          el.style.opacity = '1';
          el.style.pointerEvents = '';
          return;
        }
        importModal.classList.remove('open');
        await refreshSessions();
        selectSession(result.id);
        toast('Session imported');
      });
    });
  }

  cbcDriveSelect.addEventListener('change', () => {
    const drive = cbcDriveSelect.value;
    if (!drive) {
      cbcProjectSelect.innerHTML = '<option value="">Project</option>';
      importSessionListEl.innerHTML = '<div class="im-loading">Select a project.</div>';
      return;
    }
    buildProjectSelect(drive);
    if (currentProjectDir) {
      importSessionCountEl.textContent = '';
      importSessionListEl.innerHTML = '<div class="im-loading">Loading...</div>';
      fetchCbcSessions(currentProjectDir)
        .then((sessions: CbcSessionItem[]) => renderCbcSessions(sessions))
        .catch((e: Error) => { importSessionListEl.innerHTML = `<div class="im-loading" style="color:#f85149">${esc(e.message)}</div>`; });
    }
  });


  kimiWorkspaceSelect.addEventListener('change', () => {
    currentKimiCwd = kimiWorkspaceSelect.value;
    if (!currentKimiCwd) return;
    importSessionCountEl.textContent = '';
    importSessionListEl.innerHTML = '<div class="im-loading">Loading...</div>';
    fetchKimiSessions(currentKimiCwd)
      .then((sessions: KimiSessionItem[]) => renderKimiSessions(sessions))
      .catch((e: Error) => { importSessionListEl.innerHTML = `<div class="im-loading" style="color:#f85149">${esc(e.message)}</div>`; });
  });

  cbcProjectSelect.addEventListener('change', () => {
    currentProjectDir = cbcProjectSelect.value;
    if (!currentProjectDir) return;
    importSessionCountEl.textContent = '';
    importSessionListEl.innerHTML = '<div class="im-loading">Loading...</div>';
    fetchCbcSessions(currentProjectDir)
      .then((sessions: CbcSessionItem[]) => renderCbcSessions(sessions))
      .catch((e: Error) => { importSessionListEl.innerHTML = `<div class="im-loading" style="color:#f85149">${esc(e.message)}</div>`; });
  });

  closeImportModal.addEventListener('click', () => {
    importModal.classList.remove('open');
  });

  importModal.addEventListener('click', (e: MouseEvent) => {
    if (e.target === importModal) {
      importModal.classList.remove('open');
    }
  });

  // ── New Session Modal ──
  const newSessionModal = document.getElementById('newSessionModal') as HTMLElement;
  const closeNewSessionModal = document.getElementById('closeNewSessionModal') as HTMLElement;
  const nsCreateBtn = document.getElementById('nsCreateBtn') as HTMLElement;
  const nsNameInput = document.getElementById('nsNameInput') as HTMLInputElement;
  const nsWorkdirInput = document.getElementById('nsWorkdirInput') as HTMLInputElement;

  closeNewSessionModal.addEventListener('click', () => {
    newSessionModal.classList.remove('open');
  });
  newSessionModal.addEventListener('click', (e: MouseEvent) => {
    if (e.target === newSessionModal) {
      newSessionModal.classList.remove('open');
    }
  });
  nsCreateBtn.addEventListener('click', () => {
    let name = nsNameInput.value.trim();
    if (!name) {
      let n = 1;
      do {
        name = 'session-' + (modelData.length + n);
        n++;
      } while (modelData.find((s: Session) => s.name === name || s.id === '__pending_' + name));
    }
    const workdir = nsWorkdirInput.value.trim() || null;
    const adapter = (document.getElementById('nsAdapterSelect') as HTMLSelectElement)?.value || 'cbc';
    newSessionModal.classList.remove('open');
    _doCreateSession(name, workdir, adapter);
  });
  nsNameInput.addEventListener('keydown', (e: KeyboardEvent) => {
    if (e.key === 'Enter') {
      nsCreateBtn.click();
    }
  });
  nsWorkdirInput.addEventListener('keydown', (e: KeyboardEvent) => {
    if (e.key === 'Enter') {
      nsCreateBtn.click();
    }
  });

  // ── Manage Modal ──
  const manageModal = document.getElementById('manageModal') as HTMLElement;
  const closeManageModal = document.getElementById('closeManageModal') as HTMLElement;
  closeManageModal.addEventListener('click', () => {
    manageModal.classList.remove('open');
  });
  manageModal.addEventListener('click', (e: MouseEvent) => {
    if (e.target === manageModal) {
      manageModal.classList.remove('open');
    }
  });

  // ── Postbox Modal ──
  const postboxModal = document.getElementById('postboxModal') as HTMLElement;
  const closePostboxModal = document.getElementById('closePostboxModal') as HTMLElement;
  closePostboxModal.addEventListener('click', () => {
    postboxModal.classList.remove('open');
  });
  postboxModal.addEventListener('click', (e: MouseEvent) => {
    if (e.target === postboxModal) {
      postboxModal.classList.remove('open');
    }
  });
}

function buildModelSelect(): void {
  const sel = document.getElementById('settingModel') as HTMLSelectElement;
  sel.innerHTML = '';
  allModels().forEach((m: string) => {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = m;
    sel.appendChild(opt);
  });
  const cust = document.createElement('option');
  cust.value = '__custom__';
  cust.textContent = '\u270e custom\u2026';
  sel.appendChild(cust);
  sel.value = defaultModel();
  sel.onchange = function () {
    (document.getElementById('settingModelCustom')!).style.display =
      sel.value === '__custom__' ? 'inline-block' : 'none';
    updateSetButtonVisibility();
  };
  sel.setAttribute('data-loaded', '1');
}

function buildModeSelect(): void {
  const sel = document.getElementById('settingMode') as HTMLSelectElement;
  sel.innerHTML = '';
  permissionModes().forEach((p: {value: string; label: string}) => {
    const opt = document.createElement('option');
    opt.value = p.value;
    opt.textContent = p.label;
    sel.appendChild(opt);
  });
}

function buildEffortSelect(): void {
  const sel = document.getElementById('settingEffort') as HTMLSelectElement;
  sel.innerHTML = '';
  effortValues().forEach((v: string) => {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = v;
    sel.appendChild(opt);
  });
}

function esc<T extends HTMLElement | string>(s: T): string {
  const d = document.createElement('div');
  d.textContent = s as string;
  return d.innerHTML;
}

function copyToClipboard(text: string): void {
  if (!text) return;
  navigator.clipboard.writeText(text).then(function () {
    toast('Copied: ' + text);
  }).catch(function () {
    toast('Copy failed');
  });
}
function toast(msg: string): void {
  const el = document.getElementById('toast')!;
  el.textContent = msg;
  el.className = 'toast show';
  setTimeout(() => {
    el.className = 'toast';
  }, 3000);
}

window.addEventListener('unhandledrejection', (e: PromiseRejectionEvent) => {
  toast('Request failed');
  e.preventDefault();
});

init();
