// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { wsClient } from './ws';

class FakeWebSocket {
  static readonly OPEN = 1;
  static readonly CONNECTING = 0;
  static readonly CLOSED = 3;
  static latest: FakeWebSocket | null = null;

  readonly OPEN = FakeWebSocket.OPEN;
  readyState = FakeWebSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];

  constructor(_url: string) {
    FakeWebSocket.latest = this;
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
  }
}

function frame(overrides: Record<string, unknown>) {
  return {
    type: 'worker.stream',
    serverEpoch: 'stage3-epoch',
    eventEpoch: 'stage3-epoch',
    eventSeq: 10,
    deliveryEpoch: 'stage3-epoch',
    deliverySeq: 1,
    sourceCursorStart: 10,
    sourceCursorEnd: 10,
    ...overrides,
  };
}

describe('stage 3 WebSocket delivery/source cursors', () => {
  afterEach(() => {
    wsClient.disconnect();
    FakeWebSocket.latest = null;
    vi.unstubAllGlobals();
  });

  it('accepts a coalesced source range but consumes duplicate and old frames once', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const received: unknown[] = [];
    const gaps: unknown[] = [];
    const offReceived = wsClient.on('worker.stream', (event) => received.push(event));
    const offGap = wsClient.on('resync_required', (event) => gaps.push(event));

    wsClient.connect();
    const socket = FakeWebSocket.latest!;
    socket.onopen?.();
    socket.onmessage?.({
      data: JSON.stringify(frame({
        serverEpoch: 'stage3-epoch-gap',
        eventEpoch: 'stage3-epoch-gap',
        deliveryEpoch: 'stage3-epoch-gap',
        deliverySeq: 100,
        eventSeq: 100,
        sourceCursorStart: 100,
        sourceCursorEnd: 100,
      })),
    } as MessageEvent);
    socket.onmessage?.({
      data: JSON.stringify(frame({
        eventSeq: 12,
        deliverySeq: 2,
        sourceCursorStart: 11,
        sourceCursorEnd: 12,
      })),
    } as MessageEvent);
    // Same delivery cursor and a late source cursor are both idempotent.
    socket.onmessage?.({
      data: JSON.stringify(frame({
        eventSeq: 12,
        deliverySeq: 2,
        sourceCursorStart: 11,
        sourceCursorEnd: 12,
      })),
    } as MessageEvent);
    socket.onmessage?.({
      data: JSON.stringify(frame({
        eventSeq: 11,
        deliverySeq: 3,
        sourceCursorStart: 11,
        sourceCursorEnd: 11,
      })),
    } as MessageEvent);

    expect(received).toHaveLength(2);
    expect(gaps).toHaveLength(0);
    expect(wsClient.getEventCursor()).toEqual({ eventEpoch: 'stage3-epoch', eventSeq: 12 });
    offReceived();
    offGap();
  });

  it('emits one recovery signal for a delivery gap and announces a new server epoch', () => {
    vi.stubGlobal('WebSocket', FakeWebSocket);
    const gaps: unknown[] = [];
    const epochs: unknown[] = [];
    const offGap = wsClient.on('resync_required', (event) => gaps.push(event));
    const offEpoch = wsClient.on('server_epoch_changed', (event) => epochs.push(event));

    wsClient.connect();
    const socket = FakeWebSocket.latest!;
    socket.onopen?.();
    socket.onmessage?.({ data: JSON.stringify(frame({})) } as MessageEvent);
    socket.onmessage?.({
      data: JSON.stringify(frame({ deliverySeq: 102, eventSeq: 102, sourceCursorStart: 102, sourceCursorEnd: 102 })),
    } as MessageEvent);
    socket.onmessage?.({
      data: JSON.stringify(frame({
        serverEpoch: 'stage3-epoch-new',
        eventEpoch: 'stage3-epoch-new',
        deliveryEpoch: 'stage3-epoch-new',
        deliverySeq: 1,
        eventSeq: 1,
        sourceCursorStart: 1,
        sourceCursorEnd: 1,
      })),
    } as MessageEvent);

    expect(gaps).toHaveLength(1);
    expect(epochs.at(-1)).toMatchObject({ serverEpoch: 'stage3-epoch-new' });
    offGap();
    offEpoch();
  });
});
