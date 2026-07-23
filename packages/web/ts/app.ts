// ── Types ──

interface Message {
  role: string;
  content: string;
}

interface Session {
  id: string;
  name: string;
  adapter?: string;
  cbcSessionId?: string | null;
  model?: string | null;
  permissionMode?: string | null;
  alwaysThinkingEnabled: boolean;
  effort: string;
  maxThinkingTokens?: number;
  workdir?: string;
  workerStatus?: string | null;
  workerId?: string | null;
  history: Message[];
  historyTruncated?: boolean;
  historyTotal?: number;
  lastResult?: Record<string, unknown> | null;
  totalUsage?: Record<string, number> | null;
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
  cbc_session_id?: string;
}

interface StreamEvent {
  type: string;
  sessionId?: string;
  workerId?: string;
  event?: WorkerEvent;
  message?: string;
  status?: string;
  name?: string;
  cbcSessionId?: string;
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
  cbcSessionId?: string;
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

// ── State ──

let availableAdapters: string[] = [];
const adapterConfigs: Map<string, AdapterConfig> = new Map();
let currentAdapter: string = 'cbc';
let _adapterConfigReady: boolean = false;

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
let lastSyncedSettings: SyncedSettings | null = null;
let bubbleViewEnabled: boolean = true;
let currentHistory: Message[] = [];
let toolGroupOpen: boolean = false;
let _currentToolGroupStart: number = -1;
let _rendering: boolean = false;
let _historyLoading: boolean = false;
let _historyLoadEnd: number = 0;
const _inputDrafts: Map<string, string> = new Map();
/** Per-session set of unread thinking/tool content hashes */
const _sessionUnread: Map<string, Set<string>> = new Map();

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
  // In-place sidebar dot update (avoid full list rebuild flicker)
  if (sessionId) {
    const items = document.querySelectorAll('.sess-item');
    items.forEach(function (item) {
      const div = item as HTMLElement;
      if (div.dataset.sessionId === sessionId) {
        const dot = div.querySelector('.s-dot');
        if (dot) {
          const newClass = 's-dot ' + (status || 'offline');
          dot.className = newClass;
        }
      }
    });
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
  fetch('/api/sessions')
    .then((r: Response) => r.json())
    .then((data: ApiSessionsResponse) => {
      if (version !== _refreshVersion) return;
      modelData = data.sessions || [];
      renderSessionList();
      const matched = modelData.find((s: Session) => s.id === currentSessionId);
      if (!matched) {
        currentSessionId = null;
        currentWorkerId = null;
        showEmpty();
      } else {
        currentWorkerId = matched.workerId?? null;
        const chatNameEl = document.getElementById('chatName')!;
        if (chatNameEl.style.display !== 'none') {
          // Skip full rebuild if the tail of the server's history is already
          // rendered. Local DOM may contain more older messages; rebuilding
          // would throw them away.
          if (_shouldRenderMessages(currentSessionId, matched.history || [])) {
            renderMessages(matched.history || []);
          }
        }
      }
      if (currentSessionId) updateTopBar();
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
    div.className = 'sess-item' + (s.id === currentSessionId? ' active' : '');
    div.dataset.sessionId = s.id;
    div.onclick = function (e: MouseEvent) {
      const target = e.target as HTMLElement;
      if (target.closest('.sess-del')) return;
      if (s.id.indexOf('__pending_') === 0) return; // Placeholder — not a real session yet
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
    div.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:start">' +
      '<div class="sess-name"><span class="s-dot ' +
      (s.workerStatus || 'offline') +
      '"></span>' +
      esc(s.name) +
      '</div>' +
      '<button class="sess-del" onclick="toggleSessMenu(event,\'' +
      s.id +
      '\')" title="Session actions" style="background:none;border:none;color:#484f58;cursor:pointer;font-size:.85rem;padding:0 2px">\u2699</button>' +
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
      // Restore scroll position so visible content stays put
      if (ref) {
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
  var cbcId = s.cbcSessionId;
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
  return el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_BOTTOM_THRESHOLD;
}

function scrollMessages(): void {
  if (isNearBottom()) {
    scrollToBottom();
  } else {
    updateScrollToBottomBtn();
  }
}

function updateScrollToBottomBtn(): void {
  const el = document.getElementById('messages')!;
  const btn = document.getElementById('scrollBottomBtn') as HTMLButtonElement;
  if (!btn) return;
  btn.style.display = isNearBottom() || el.scrollHeight <= el.clientHeight? 'none' : '';
}

function scrollToBottom(): void {
  const el = document.getElementById('messages')!;
  el.scrollTop = el.scrollHeight;
  updateScrollToBottomBtn();
}

// ── Messages ──

function renderMessages(history: Message[]): void {
  currentHistory = history || [];
  _recordRenderedFor(currentSessionId, currentHistory);
  _currentToolGroupStart = -1;
  _rendering = true;
  const el = document.getElementById('messages')!;
  el.innerHTML = '';
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
    scrollMessages();
    return;
  }
  // start a new tool-group
  _currentToolGroupStart = currentHistory.length - 1;
  const wrapper = _createToolGroupEl([{ role: 'tool', content: content }]);
  el.appendChild(wrapper);
  scrollMessages();
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
        .writeText('cbc --resume ' + (d.cbcSessionId?? ''))
        .then(() => {
          toast('PowerShell opened. Session copied to clipboard.');
        })
        .catch(() => {
          toast('PowerShell opened for takeover.');
        });
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

// ── Send message ──

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
    toast('Worker is busy');
    return;
  }
  input.value = '';
  _inputDrafts.delete(currentSessionId);

  addMessage('user', text);

  function doSend(): void {
    const msg = JSON.stringify({
      type: 'user_inject',
      sessionId: currentSessionId,
      text: text,
    });
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(msg);
      return;
    }
    if (ws.readyState === WebSocket.CONNECTING) {
      // Wait for connection to open (common on slow mobile networks)
      ws.addEventListener('open', function handler() {
        ws.removeEventListener('open', handler);
        ws.send(msg);
      }, { once: true } as any);
      return;
    }
    // CLOSED or CLOSING — give up
    toast('Connection lost. Please refresh the page.');
  }

  if (!currentWorkerId) {
    const body: Record<string, unknown> = {
      sessionId: currentSessionId,
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
          return;
        }
        currentWorkerId = d.workerId?? null;
        doSend();
      });
    return;
  }

  // worker exists: if panel has unapplied changes, apply them first, then send
  if (hasPendingChanges()) {
    const thinking = (document.getElementById('settingThinking') as HTMLInputElement).checked;
    const effort = (document.getElementById('settingEffort') as HTMLSelectElement).value;
    fetch('/api/worker/' + currentWorkerId + '/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_buildSettingsBody()),
    })
      .then((r: Response) => r.json())
      .then((d: ApiGenericResponse) => {
        if (d.error) { toast(d.error); return; }
        markSettingsApplied();
        doSend();
      });
    return;
  }

  doSend();
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
    (s.cbcSessionId
      ? '<div class="sess-menu-item" onclick="closeSessMenu();reimportSession(\'' + id + '\')">\u21BB Reimport</div>' +
        '<div class="sess-menu-item" onclick="closeSessMenu();branchSession(\'' + id + '\')">\u2442 Branch</div>'
      : '') +
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
  if (!s || !s.cbcSessionId) return;
  const cwd = s.workdir || '';
  const body: Record<string, string> = { session_id: s.cbcSessionId };
  if (cwd) body.cwd = cwd;
  fetch('/api/cbc/sessions/import', {
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
  if (!s || !s.cbcSessionId) return;
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
    opt.textContent = a;
    sel.appendChild(opt);
  });
  sel.value = currentAdapter || 'cbc';
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
