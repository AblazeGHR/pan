import { useCallback, useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { terminalRequest } from '@/services/terminal';
import type { TerminalView } from '@/services/terminal';
import { TerminalStream } from '@/services/terminalWs';
import { terminalKeyHandler } from '@/services/terminalKeyboard';
import type { TerminalState } from '@/services/terminalWs';

const initial: TerminalState = { connected: false, control: false, recovering: true, message: '请选择终端' };

export default function TerminalPanel() {
  const [records, setRecords] = useState<TerminalView[]>([]);
  const [selected, setSelected] = useState('');
  const [cwd, setCwd] = useState('');
  const [workspaceId, setWorkspaceId] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState(initial);
  const element = useRef<HTMLDivElement>(null);
  const stream = useRef<TerminalStream | null>(null);
  const endedSelection = useRef<string | undefined>(undefined);
  const refresh = useCallback(async () => {
    try { setRecords(await terminalRequest<TerminalView[]>()); setError(''); }
    catch (reason) { setError(reason instanceof Error ? reason.message : '终端列表不可用'); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!selected || !element.current) return;
    if (endedSelection.current) {
      setState({ connected: false, control: false, recovering: false,
        terminalStatus: endedSelection.current,
        message: endedSelection.current === 'exited'
          ? '终端已退出；历史屏幕未持久化，不能重连旧进程。'
          : '终端记录为 lost；请核对服务端清理状态。' });
      return;
    }
    let alive = true;
    const terminal = new Terminal({ rows: 24, cols: 80, scrollback: 1000, fontSize: 13,
      theme: { background: '#15171b', foreground: '#e7e9ee' }, allowProposedApi: false });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(element.current);
    terminal.attachCustomKeyEventHandler(terminalKeyHandler);
    let wasControl = false;
    let terminalRecordRefreshed = false;
    const connection = new TerminalStream(selected, terminal, (next) => {
      if (alive) {
        setState(next);
        if (next.control && !wasControl) fitScreen();
        wasControl = next.control;
        if (next.terminalStatus && !terminalRecordRefreshed) {
          terminalRecordRefreshed = true;
          void refresh(); // keep the selected tail screen; refresh record facts only
        }
      }
    });
    stream.current = connection;
    const url = new URL(`/ws/terminal/${encodeURIComponent(selected)}?cursor=0`, location.href);
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(url);
    connection.bind(socket);
    socket.onmessage = (event) => { if (alive && typeof event.data === 'string') connection.receive(event.data); };
    socket.onclose = (event) => { if (alive) connection.disconnected(event.code === 1000); };
    socket.onerror = () => { if (alive) setError('连接失败：请检查同源入口与终端状态'); };
    const dataSubscription = terminal.onData((text) => connection.input(text));
    let resizeTimer: ReturnType<typeof setTimeout> | undefined;
    const fitScreen = () => {
      if (!alive || !element.current?.clientWidth || !element.current?.clientHeight) return;
      fit.fit();
      connection.resize(terminal.rows, terminal.cols);
    };
    const observer = new ResizeObserver(() => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(fitScreen, 100);
    });
    observer.observe(element.current);
    fitScreen();
    return () => {
      alive = false;
      clearTimeout(resizeTimer);
      observer.disconnect();
      dataSubscription.dispose();
      connection.dispose(); // disconnect only, never close/detach the runtime
      if (stream.current === connection) stream.current = null;
      terminal.dispose();
    };
  }, [selected, revision, refresh]);

  async function create() {
    setBusy(true);
    try {
      const view = await terminalRequest<TerminalView>('', { rows: 24, cols: 80, ...(cwd ? { cwd } : {}),
        ...(workspaceId ? { workspace_id: workspaceId } : {}), ...(sessionId ? { session_id: sessionId } : {}) });
      await refresh();
      endedSelection.current = undefined;
      setSelected(view.terminal_id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '创建失败'); }
    finally { setBusy(false); }
  }
  async function lifecycle(action: 'close' | 'detach') {
    if (!selected || !window.confirm(action === 'close' ? '终止此终端及其子进程？关闭面板不会终止它。' : '请求持久脱离？当前环境可能拒绝，拒绝时不会改变状态。')) return;
    setBusy(true);
    try {
      await terminalRequest(`/${encodeURIComponent(selected)}/${action}`, {});
      await refresh();
      if (action === 'close') {
        setSelected('');
        setState(initial);
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : '未确认完成；可以重试'); }
    finally { setBusy(false); }
  }

  return <section className="flex flex-col flex-1 min-h-0 p-3 gap-2" aria-label="全局终端">
    <header className="flex flex-wrap items-center gap-2">
      <h1 className="font-semibold">全局终端</h1>
      <input aria-label="工作目录" placeholder="工作目录（留空用默认值）" value={cwd} onChange={(event) => setCwd(event.target.value)}
        className="bg-bg-tertiary border border-border-default rounded px-2 py-1 text-sm" />
      <input aria-label="关联工作区 ID" placeholder="关联工作区 ID（可选）" value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}
        className="bg-bg-tertiary border border-border-default rounded px-2 py-1 text-sm" />
      <input aria-label="关联 Session ID" placeholder="关联 Session ID（可选）" value={sessionId} onChange={(event) => setSessionId(event.target.value)}
        className="bg-bg-tertiary border border-border-default rounded px-2 py-1 text-sm" />
      <button disabled={busy} onClick={() => void create()} className="px-2 py-1 rounded bg-accent/20">新建终端</button>
      <button onClick={() => void refresh()} className="px-2 py-1">刷新列表</button>
    </header>
    <div className="flex flex-wrap gap-2 items-center">
      <select aria-label="选择终端" value={selected} onChange={(event) => {
        const selectedRecord = records.find(record => record.terminal_id === event.target.value);
        endedSelection.current = selectedRecord && ['exited', 'lost'].includes(selectedRecord.status) ? selectedRecord.status : undefined;
        setSelected(event.target.value); setState(initial);
      }}
        className="bg-bg-tertiary border border-border-default rounded px-2 py-1">
        <option value="">选择终端</option>
        {records.map((record) => <option key={record.terminal_id} value={record.terminal_id}>
          {record.terminal_id} · {record.status}{record.detached ? ' · detached' : ''}
          {record.scope?.workspace_id ? ` · 工作区 ${record.scope.workspace_id}` : ''}
          {record.scope?.session_id ? ` · Session ${record.scope.session_id}` : ''}
        </option>)}
      </select>
      <button disabled={!selected || state.control || !state.connected} onClick={() => stream.current?.claim()}>取得输入控制权</button>
      <button disabled={!state.control} onClick={() => stream.current?.release()}>释放控制权</button>
      <button disabled={!selected || !state.connected} onClick={() => stream.current?.snapshot()}>重取服务器屏幕</button>
      <button disabled={!selected || state.terminalStatus === 'exited' || state.terminalStatus === 'lost'} onClick={() => { setState(initial); setRevision((value) => value + 1); }}>重连</button>
      <button disabled={!selected || busy || state.terminalStatus === 'exited' || state.terminalStatus === 'lost'} onClick={() => void lifecycle('detach')}>持久脱离</button>
      <button disabled={!selected || busy} onClick={() => void lifecycle('close')} className="text-red-400">终止终端</button>
    </div>
    <p role="status" className="text-xs text-text-secondary">{state.control ? '控制模式' : '只观察'} · {state.message}</p>
    <p className="text-xs text-text-tertiary">屏幕恢复范围：{state.recovery || '未确认'}（控制权变化不会升级屏幕保真）</p>
    {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
    <div ref={element} className="flex-1 min-h-40 min-w-0 overflow-hidden rounded bg-[#15171b] p-2" data-testid="terminal-screen" />
    <p className="text-xs text-text-tertiary">离开本页或断线不会终止进程。屏幕恢复按服务器声明，partial 不等于完整保真；Ctrl-C 转交前台程序，具体响应由程序决定。</p>
    <p className="text-xs text-text-tertiary">关联 ID 仅为元数据，不授予权限，也不改变终端寿命。</p>
  </section>;
}
