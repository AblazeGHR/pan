import { decodeOutput, encodeInput } from './terminal';

export interface TerminalRenderer {
  write(data: Uint8Array, callback: () => void): void;
  reset(): void;
  resize(cols: number, rows: number): void;
}
interface Transport {
  readyState: number;
  send(data: string): void;
  close(): void;
}
export interface TerminalState {
  connected: boolean;
  control: boolean;
  recovering: boolean;
  message: string;
  recovery?: string;
}

function cursor(value: unknown): string | null {
  if (typeof value !== 'string' || !/^[0-9]{1,20}$/.test(value)) return null;
  const number = BigInt(value);
  return number <= 18446744073709551615n ? number.toString() : null;
}

/** One display generation, one write FIFO. Never replay input after reconnect. */
export class TerminalStream {
  private socket: Transport | null = null;
  private generation: string | null = null;
  private frontier = '0';
  private serial = Promise.resolve();
  private epoch = 0;
  private queuedBytes = 0;
  private queuedItems = 0;
  private awaitingResume = false;
  private resumingCursor: string | null = null;
  private snapshotRequested = false;
  private disposed = false;
  private disconnecting = false;
  private terminalEnded = false;
  state: TerminalState = { connected: false, control: false, recovering: true, message: '连接中' };

  constructor(private id: string, private screen: TerminalRenderer,
              private changed: (state: TerminalState) => void) {}

  bind(socket: Transport) {
    this.epoch++;
    this.socket = socket;
    this.disposed = false;
    this.disconnecting = false;
    this.terminalEnded = false;
  }
  private update(values: Partial<TerminalState>) {
    this.state = { ...this.state, ...values };
    this.changed(this.state);
  }
  private command(op: string, fields: Record<string, unknown> = {}) {
    if (!this.disposed && !this.disconnecting && this.socket?.readyState === 1) {
      this.socket.send(JSON.stringify({ v: 1, type: 'command', terminal_id: this.id, op, ...fields }));
      return true;
    }
    return false;
  }
  claim() { this.command('claim'); }
  release() { this.command('release'); }
  input(text: string) {
    if (this.generation !== null && this.state.control && !this.state.recovering) {
      this.command('input', { generation: this.generation, data_b64: encodeInput(text) });
    }
  }
  resize(rows: number, cols: number) {
    if (this.generation !== null && this.state.control) {
      this.command('resize', { rows: Math.min(500, Math.max(1, rows)), cols: Math.min(1000, Math.max(1, cols)), generation: this.generation });
    }
  }
  snapshot() {
    this.update({ recovering: true, message: '读取服务器屏幕；不会重启终端' });
    this.snapshotRequested = true;
    this.awaitingResume = false;
    this.command('snapshot', { timeout_ms: 1000 });
  }
  disconnected(graceful = false) {
    const epoch = this.epoch;
    this.disconnecting = true;
    this.generation = null;
    this.update({ connected: false, control: false });
    const finish = () => {
      if (epoch !== this.epoch || this.disposed) return;
      this.epoch++;
      this.snapshotRequested = false;
      if (graceful && this.terminalEnded) {
        this.update({ connected: false, control: false, recovering: false });
      } else {
        this.update({ connected: false, control: false, recovering: true, recovery: 'unknown', message: '已断开；可重连核对服务器终端状态' });
      }
    };
    // A normal close frame follows the server's final queued output/state.
    // Preserve their rendering FIFO; abnormal disconnect still invalidates it.
    if (graceful) this.serial = this.serial.then(finish);
    else finish();
  }
  dispose() {
    this.disposed = true;
    this.epoch++;
    this.socket?.close();
    this.socket = null;
  }
  receive(text: string) {
    if (this.disposed || this.disconnecting) return;
    const size = new TextEncoder().encode(text).length;
    if (this.queuedBytes + size > 4 * 1024 * 1024 || this.queuedItems >= 256) {
      this.socket?.close();
      this.disconnected();
      this.update({ message: '渲染积压超限；重连后从服务器屏幕恢复' });
      return;
    }
    this.queuedBytes += size;
    this.queuedItems++;
    const epoch = this.epoch;
    this.serial = this.serial.then(async () => {
      if (epoch !== this.epoch || this.disposed) return;
      const event = JSON.parse(text);
      if (event.v !== 1 || event.terminal_id !== this.id) return;
      switch (event.type) {
        case 'hello':
          if (this.disconnecting) break;
          this.generation = null;
          this.update({ connected: true, control: false });
          this.snapshot();
          break;
        case 'claim-result':
          if (this.disconnecting) break;
          this.generation = cursor(event.generation);
          this.update({ control: this.generation !== null, message: '已取得输入控制权（可被其它连接抢占）' });
          break;
        case 'release-result':
          this.generation = null;
          this.update({ control: false, message: '观察模式' });
          break;
        case 'snapshot': {
          if (!this.snapshotRequested) break;
          this.snapshotRequested = false;
          const position = cursor(event.cursor);
          if (position === null || event.cursors_valid !== true || event.reset_unconfirmed === true ||
              event.applied_evicted === true || !['full', 'partial'].includes(event.recovery) ||
              typeof event.data_b64 !== 'string') {
            this.update({ recovering: true, recovery: 'unconfirmed', message: '屏幕基线不可确认或已被驱逐；请重取屏幕，不会自动 reset PTY' });
            break;
          }
          if (Number.isInteger(event.rows) && Number.isInteger(event.cols) && event.rows > 0 && event.cols > 0) {
            this.screen.resize(event.cols, event.rows);
          }
          this.screen.reset();
          await this.write(decodeOutput(event.data_b64));
          if (epoch !== this.epoch) break;
          this.frontier = position;
          this.resumingCursor = position;
          this.awaitingResume = true;
          this.command('resume', { cursor: position });
          this.update({ recovery: event.recovery === 'full' && event.fidelity === 'full' ? 'full' : 'partial', message: event.recovery === 'full' && event.fidelity === 'full'
            ? '服务器屏幕恢复（已验证能力范围）' : `部分屏幕恢复；未验证模式不保证保真${event.note ? '：' + String(event.note).slice(0, 200) : ''}` });
          break;
        }
        case 'resume-result':
          if (this.awaitingResume && cursor(event.cursor) === this.resumingCursor) {
            this.awaitingResume = false;
            this.update({ recovering: false });
          }
          break;
        case 'output': {
          if (this.state.recovering || this.snapshotRequested || this.awaitingResume) break;
          const start = cursor(event.seq), end = cursor(event.next_seq);
          if (start !== this.frontier || end === null || typeof event.data_b64 !== 'string') {
            this.update({ recovering: true, message: '输出不连续；需重新读取服务器屏幕' });
            break;
          }
          const bytes = decodeOutput(event.data_b64);
          if (BigInt(end) - BigInt(start) !== BigInt(bytes.length)) throw new Error('invalid-output-range');
          await this.write(bytes);
          if (epoch !== this.epoch) break;
          this.frontier = end;
          this.command('ack', { next_seq: end }); // after xterm callback, never on enqueue
          break;
        }
        case 'gap':
          this.update({ recovering: true, message: '输出日志有缺口；请重取服务器屏幕，不补零、不自动重启' });
          break;
        case 'error':
          if (event.code === 'stale-generation' || event.code === 'not-control') {
            this.generation = null;
            this.update({ control: false });
          }
          this.update({ message: `终端请求未完成：${event.code}` });
          break;
        case 'terminal-state':
          this.generation = null;
          this.terminalEnded = true;
          this.update({ control: false, message: `终端状态：${event.status}${Number.isSafeInteger(event.exit_code) ? `（退出码 ${event.exit_code}）` : ''}；${event.output_complete === true ? '输出已确认完整' : '输出完整性未确认'}` });
          break;
      }
    }).catch(() => {
      this.update({ recovering: true, message: '输出协议或渲染失败；需重取屏幕' });
    }).finally(() => { this.queuedBytes -= size; this.queuedItems--; });
  }
  private write(bytes: Uint8Array): Promise<void> {
    return new Promise((resolve) => this.screen.write(bytes, resolve));
  }
  settled() { return this.serial; }
}
