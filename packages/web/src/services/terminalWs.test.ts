import { describe, expect, it } from 'vitest';
import { TerminalStream } from './terminalWs';
import { decodeOutput, encodeInput } from './terminal';

function fixture() {
  const writes: Uint8Array[] = [], pending: (() => void)[] = [];
  let hold = false, resets = 0, closed = 0;
  const commands: Record<string, unknown>[] = [];
  const socket = { readyState: 1, send: (value: string) => commands.push(JSON.parse(value)), close: () => closed++ };
  const screen = { reset: () => resets++, resize: () => {}, write: (data: Uint8Array, callback: () => void) => {
    writes.push(data); if (hold) pending.push(callback); else callback();
  } };
  const stream = new TerminalStream('term_1', screen, () => {});
  stream.bind(socket);
  const event = (type: string, fields = {}) => stream.receive(JSON.stringify({ v: 1, terminal_id: 'term_1', type, ...fields }));
  const snapshot = (fields = {}) => event('snapshot', { cursor: '9007199254740993', cursors_valid: true, reset_unconfirmed: false,
    recovery: 'partial', fidelity: 'partial', rows: 24, cols: 80, data_b64: encodeInput('screen'), ...fields });
  return { stream, commands, writes, pending, event, snapshot, hold: () => { hold = true; }, resets: () => resets, closed: () => closed };
}

describe('terminal display protocol A', () => {
  it('preserves UTF-8 bytes without Number cursor conversion', () => {
    expect(new TextDecoder().decode(decodeOutput(encodeInput('中文\r\n')))).toBe('中文\r\n');
  });
  it('restores once then resumes applied cursor, without replaying pending tail', async () => {
    const f = fixture();
    f.event('hello'); await f.stream.settled();
    f.event('output', { seq: '0', next_seq: '3', data_b64: encodeInput('OLD') });
    f.snapshot(); await f.stream.settled();
    expect(f.resets()).toBe(1);
    expect(f.writes).toHaveLength(1);
    expect(f.commands.at(-1)).toMatchObject({ op: 'resume', cursor: '9007199254740993' });
    expect(f.stream.state.recovering).toBe(true);
    f.event('resume-result', { cursor: '9007199254740993' }); await f.stream.settled();
    f.event('output', { seq: '9007199254740993', next_seq: '9007199254740994', data_b64: encodeInput('x') });
    await f.stream.settled();
    expect(f.commands.at(-1)).toMatchObject({ op: 'ack', next_seq: '9007199254740994' });
    expect(f.stream.state.message).toContain('部分');
  });
  it('does not acknowledge until renderer write callback confirms completion', async () => {
    const f = fixture(); f.event('hello'); await f.stream.settled();
    f.snapshot({ cursor: '0' }); await f.stream.settled();
    f.event('resume-result', { cursor: '0' }); await f.stream.settled();
    f.hold(); f.event('output', { seq: '0', next_seq: '1', data_b64: encodeInput('x') });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(f.commands.some((command) => command.op === 'ack')).toBe(false);
    f.pending.shift()?.(); await f.stream.settled();
    expect(f.commands.at(-1)?.op).toBe('ack');
  });
  it.each([{ cursors_valid: null }, { cursors_valid: false }, { applied_evicted: true },
    { reset_unconfirmed: true }, { recovery: 'degraded' }, { cursor: '1e3' }])('does not resume unconfirmed baseline %j', async (fields) => {
    const f = fixture(); f.event('hello'); await f.stream.settled();
    f.snapshot(fields); await f.stream.settled();
    expect(f.commands.filter((command) => command.op === 'resume')).toHaveLength(0);
    expect(f.resets()).toBe(0);
  });
  it('requires actual control generation; disconnect never replays input', async () => {
    const f = fixture(); f.stream.input('x'); expect(f.commands).toHaveLength(0);
    f.event('hello'); await f.stream.settled(); f.snapshot({ cursor: '0' }); await f.stream.settled();
    f.event('resume-result', { cursor: '0' }); f.event('claim-result', { generation: '9007199254740993' }); await f.stream.settled();
    f.stream.input('中文');
    expect(f.commands.at(-1)).toMatchObject({ op: 'input', generation: '9007199254740993', data_b64: encodeInput('中文') });
    const count = f.commands.length; f.stream.disconnected(); f.stream.input('never replay');
    expect(f.commands).toHaveLength(count);
  });
  it('gap pauses rendering and requires explicit recovery without runtime reset', async () => {
    const f = fixture(); f.event('hello'); await f.stream.settled(); f.snapshot({ cursor: '0' }); await f.stream.settled();
    f.event('resume-result', { cursor: '0' }); f.event('gap'); await f.stream.settled();
    f.event('output', { seq: '0', next_seq: '1', data_b64: encodeInput('x') }); await f.stream.settled();
    expect(f.writes).toHaveLength(1);
    expect(f.commands.some((command) => command.op === 'reset')).toBe(false);
  });
  it('bounds queued frames including the pending renderer write', async () => {
    const f = fixture(); f.event('hello'); await f.stream.settled(); f.hold(); f.snapshot();
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 257; i++) f.event('pong');
    expect(f.closed()).toBeGreaterThan(0);
    f.pending.shift()?.(); await f.stream.settled();
  });
});
