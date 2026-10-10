import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { nativeRequest, type NativeOutput, type NativeControlState } from '@/services/nativeControl';
import { useNativeControlStore } from '@/stores/nativeControlStore';

export function NativeTerminal({ sessionId, state }: { sessionId: string; state: NativeControlState }) {
  const host = useRef<HTMLDivElement>(null);
  const currentState = useRef(state);
  currentState.current = state;
  const [error, setError] = useState('');
  useEffect(() => { setError(''); }, [state.workerId, state.generation]);
  useEffect(() => {
    if (!host.current) return;
    const terminal = new Terminal({ cursorBlink: true, fontSize: 14, convertEol: false,
      theme: { background: '#111318', foreground: '#e4e6eb' } });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(host.current);
    let stopped = false;
    let cursor = '0';
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    // Serialize inputs so escape sequences and paste chunks never reorder.
    let writes = Promise.resolve();
    const write = (payload: object) => {
      const identity = currentState.current;
      writes = writes.then(async () => {
        if (stopped || identity.mode !== 'native') return;
        await nativeRequest(sessionId, '/write', { ...payload, workerId: identity.workerId,
          generation: identity.generation }, controller.signal);
      }).catch(() => { if (!stopped) setError('Input was not confirmed; switch control again before retrying.'); });
    };
    const data = terminal.onData((text) => {
      // Chunk by Unicode code points: never split a UTF-8 character/surrogate.
      let chunk = ''; let bytes = 0;
      const encoder = new TextEncoder();
      for (const character of text) {
        const size = encoder.encode(character).length;
        if (bytes + size > 32768) { write({ text: chunk }); chunk = ''; bytes = 0; }
        chunk += character; bytes += size;
      }
      if (chunk) write({ text: chunk });
    });
    const resize = terminal.onResize(({ rows, cols }) => write({ rows, cols }));
    const observer = new ResizeObserver(() => fit.fit());
    observer.observe(host.current);
    fit.fit();
    terminal.focus();
    const poll = async () => {
      try {
        const output = await nativeRequest<NativeOutput>(sessionId, `/output?cursor=${cursor}`, undefined, controller.signal);
        if (stopped) return;
        if (output.workerId !== state.workerId) { setError('The agent process changed. Re-enter Terminal TUI.'); return; }
        if (output.gap) {
          terminal.reset();
          setError('Terminal output exceeded the replay buffer; this view may be incomplete. The agent continues running.');
        }
        if (output.data) {
          const bytes = Uint8Array.from(atob(output.data), (char) => char.charCodeAt(0));
          await new Promise<void>((resolve) => terminal.write(bytes, resolve));
        }
        cursor = output.cursor;
        if (output.mode !== currentState.current.mode) void useNativeControlStore.getState().refresh(sessionId);
      } catch { if (!stopped) setError('Terminal connection unavailable. The agent was not stopped.'); }
      if (!stopped) timer = setTimeout(() => void poll(), 80);
    };
    void poll();
    return () => {
      stopped = true; controller.abort(); clearTimeout(timer);
      observer.disconnect(); data.dispose(); resize.dispose(); terminal.dispose();
      // Detach only the browser view. Never close the resident TUI or kernel.
    };
  }, [sessionId, state.workerId]);
  return <div className="flex flex-1 min-h-0 flex-col" data-testid="native-terminal">
    {(error || state.error || state.mode === 'switching') && <div role="alert" className="px-3 py-1 text-xs text-danger">
      {error || state.error || 'Control ownership is unconfirmed; input is suspended.'}
    </div>}
    <div ref={host} className="flex-1 min-h-0 p-2 bg-[#111318]" />
  </div>;
}
