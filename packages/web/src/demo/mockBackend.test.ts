// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  handoffSession,
  deleteSessionQueueItem,
  enqueueSessionMessage,
  fetchSessionQueue,
  reorderSessionQueue,
  updateSessionQueueItem,
} from '@/services/api';
import {
  installMockBackend,
  isMockMode,
  mockSessions,
  mockUploadSessionAttachment,
  resetMockData,
} from './mockBackend';
import { ManualReportRejectedError, reportTaskManually } from '@/services/api';

describe('frontend mock backend', () => {
  let originalFetch: typeof window.fetch;

  beforeEach(() => {
    originalFetch = window.fetch;
    if (typeof window.fetch !== 'function') {
      window.fetch = vi.fn(async () => new Response('{}'));
    }
    resetMockData();
    installMockBackend();
  });

  afterEach(() => {
    window.fetch = originalFetch;
    resetMockData();
    window.history.replaceState({}, '', '/');
  });

  it('rejects handoff instead of pretending relationship transfer succeeded', async () => {
    await expect(handoffSession('mock-alpha', { handoffPrompt: 'brief' })).rejects.toThrow('演示模式不支持替身交接');
  });

  it('only enables the URL switch for ?mock=1', () => {
    window.history.pushState({}, '', '/?mock=0');
    expect(isMockMode()).toBe(false);
    window.history.pushState({}, '', '/?mock=1');
    expect(isMockMode()).toBe(true);
  });

  it('simulates upload progress and returns a safe attachment reference', async () => {
    const progress: Array<[number, number]> = [];
    const file = new File(['demo'], '说明.md', { type: 'text/markdown' });

    const result = await mockUploadSessionAttachment('mock-alpha', file, (loaded, total) => {
      progress.push([loaded, total]);
    });

    expect(progress[0]).toEqual([0, file.size]);
    expect(progress.at(-1)).toEqual([file.size, file.size]);
    expect(result).toMatchObject({
      ok: true,
      filename: '说明.md',
      attachmentId: expect.stringMatching(/^upload_[a-z0-9]{32}\.md$/),
      displayName: '说明.md',
      path: 'D:\\mock-uploads\\mock-alpha\\说明.md',
      size: file.size,
    });
    expect(result.href).toMatch(/^\/api\/attachments\/upload_[a-z0-9]{32}\.md\?session_id=mock-alpha$/);
  });

  it('serves the existing queue API contract for mock send and queue actions', async () => {
    const first = await enqueueSessionMessage('mock-alpha', 'first message', 'client-1');
    const duplicate = await enqueueSessionMessage('mock-alpha', 'changed duplicate', 'client-1');
    const second = await enqueueSessionMessage('mock-alpha', 'second message', 'client-2');

    expect(first.item).toMatchObject({ text: 'first message', kind: 'task', source: 'user' });
    expect(duplicate).toMatchObject({ duplicate: true, item: { id: first.item.id, text: 'first message' } });
    expect(second.queueRevision).toBe(2);

    const initial = await fetchSessionQueue('mock-alpha');
    expect(initial.map((item) => item.text)).toEqual(['first message', 'second message']);
    expect(initial.queueRevision).toBe(2);

    const reordered = await reorderSessionQueue(
      'mock-alpha',
      [second.item.id, first.item.id],
      initial.queueRevision,
    );
    expect(reordered.map((item) => item.id)).toEqual([second.item.id, first.item.id]);

    const edited = await updateSessionQueueItem(
      'mock-alpha',
      first.item.id,
      'edited message',
      reordered.queueRevision,
    );
    expect(edited.item).toMatchObject({ id: first.item.id, text: 'edited message' });

    await deleteSessionQueueItem('mock-alpha', second.item.id);
    const final = await fetchSessionQueue('mock-alpha');
    expect(final.map((item) => item.text)).toEqual(['edited message']);
    expect(final[0]?.meta?.dispatchState).toBe('queued');
  });
});

describe('mock manual-report route (plain runs + run identity)', () => {
  let originalFetch: typeof window.fetch;

  const seed = (
    history: Array<Record<string, unknown>>,
    terminalResults: Array<Record<string, unknown>>,
  ) => {
    const session = mockSessions.find((item) => item.id === 'mock-alpha')!;
    Object.assign(session, {
      managedBy: 'mock-manager',
      history,
      historyEpoch: 'e1',
      historyRevision: history.length,
      terminalResults,
    });
    return 'mock-alpha';
  };

  const rows = [
    { role: 'user', content: 'typed', messageId: 'pan:u1' },
    { role: 'assistant', content: 'answer', messageId: 'pan:a1' },
  ];
  const plainTerminal = (extra: Record<string, unknown> = {}) => ({
    taskId: null, taskSeq: 1, resultCursor: 1, terminalKey: 't:1',
    status: 'done', historyEpoch: 'e1', historyRevision: 2, ...extra,
  });

  beforeEach(() => {
    originalFetch = window.fetch;
    if (typeof window.fetch !== 'function') {
      window.fetch = vi.fn(async () => new Response('{}'));
    }
    resetMockData();
    installMockBackend();
    window.history.replaceState({}, '', '/?mock=1');
  });

  afterEach(() => {
    window.fetch = originalFetch;
    resetMockData();
    window.history.replaceState({}, '', '/');
  });

  it('reports a plain conversation run by its taskSeq with taskId null', async () => {
    const sessionId = seed(rows, [plainTerminal()]);
    const result = await reportTaskManually(sessionId, null, 1, 'pan:a1');
    expect(result.ok).toBe(true);
    const attempts = window.__panMockManualReportLog!.attempts;
    expect(attempts.at(-1)).toMatchObject({ taskId: null, taskSeq: 1, messageId: 'pan:a1', outcome: 'delivered' });
  });

  it('reports by result cursor alone and dedups across identity paths', async () => {
    const sessionId = seed(rows, [plainTerminal({ taskSeq: null })]);
    await reportTaskManually(sessionId, null, null, 'pan:a1', 1);
    const replay = await reportTaskManually(sessionId, null, null, 'pan:a1', 1);
    expect(replay.alreadyReported).toBe(true);
    // seq↔cursor converge on one run key: the terminal carries both identities,
    // so reporting it by cursor first and by sequence second replays.
    const both = seed(rows, [plainTerminal()]);
    await reportTaskManually(both, null, null, 'pan:a1', 1);
    const bySeq = await reportTaskManually(both, null, 1, 'pan:a1');
    expect(bySeq.alreadyReported).toBe(true);
  });

  it('refuses an identity-less request instead of reporting the newest run', async () => {
    const sessionId = seed(rows, [plainTerminal()]);
    await expect(reportTaskManually(sessionId, null, null, 'pan:a1'))
      .rejects.toBeInstanceOf(ManualReportRejectedError);
    expect(window.__panMockManualReportLog!.attempts.at(-1)?.outcome).toBe('rejected');
    expect(window.__panMockManualReportLog!.deliveries.length).toBe(0);
  });

  it('mirrors the merged conflict semantics for a cursor with other identities', async () => {
    // taskId null means "plain run / not provided" and is not compared; the
    // cursor decides (exactly like the merged server).
    const nullId = seed(rows, [plainTerminal({ taskId: 't-1', taskSeq: 3, resultCursor: 9 })]);
    const resolved = await reportTaskManually(nullId, null, null, 'pan:a1', 9);
    expect(resolved.ok).toBe(true);
    // A non-null taskId or taskSeq that describes another run is refused.
    await expect(reportTaskManually(nullId, 't-other', null, 'pan:a1', 9))
      .rejects.toThrow(/不一致/);
    await expect(reportTaskManually(nullId, 't-1', 5, 'pan:a1', 9))
      .rejects.toThrow(/不一致/);
    // A terminal lacking taskSeq plus a submitted taskSeq is a mismatch too —
    // never resolved to whichever side happens to match.
    const noSeq = seed(rows, [plainTerminal({ taskId: null, taskSeq: null, resultCursor: 9 })]);
    await expect(reportTaskManually(noSeq, null, 5, 'pan:a1', 9))
      .rejects.toThrow(/不一致/);
  });

  it('drops a non-positive cursor as not provided (merged narrow fix)', async () => {
    const sessionId = seed(rows, [plainTerminal()]);
    // Cursor 0 alone: not an identity at all.
    await expect(reportTaskManually(sessionId, null, null, 'pan:a1', 0))
      .rejects.toThrow(/缺少运行身份/);
    // A raw zero cursor is not a stored identity either (production allocates
    // positive cursors only).
    seed(rows, [plainTerminal({ taskSeq: null, resultCursor: 0 })]);
    await expect(reportTaskManually(sessionId, null, null, 'pan:a1', 0))
      .rejects.toThrow(/缺少运行身份/);
    // With a legal taskSeq present, the non-positive cursor is ignored and the
    // stored sequence decides (fixture terminal carries taskSeq 1).
    seed(rows, [plainTerminal()]);
    const delivered = await reportTaskManually(sessionId, null, 1, 'pan:a1', 0);
    expect(delivered.ok).toBe(true);
  });

  it('refuses an identity-less resolved terminal', async () => {
    // A done row with neither taskSeq nor a positive cursor must never become
    // deliverable, even if some path resolved it.
    const sessionId = seed(rows, [plainTerminal({ taskSeq: null, resultCursor: undefined })]);
    await expect(reportTaskManually(sessionId, null, null, 'pan:a1', 1))
      .rejects.toThrow(/终态结果不可用|没有可证明/);
  });

  it('refuses a report when the session has no manager', async () => {
    const sessionId = seed(rows, [plainTerminal()]);
    const session = mockSessions.find((item) => item.id === 'mock-alpha')!;
    delete (session as { managedBy?: string }).managedBy;
    await expect(reportTaskManually(sessionId, null, 1, 'pan:a1'))
      .rejects.toThrow(/没有 manager/);
  });

  it('resolves a legacy terminal without resultCursor through the taskSeq-equal cursor', async () => {
    const sessionId = seed(rows, [plainTerminal({ taskSeq: 6, resultCursor: undefined })]);
    const result = await reportTaskManually(sessionId, null, null, 'pan:a1', 6);
    expect(result.ok).toBe(true);
  });

  it('rejects a wrong or unknown cursor with no_task_result', async () => {
    const sessionId = seed(rows, [plainTerminal()]);
    await expect(reportTaskManually(sessionId, null, null, 'pan:a1', 99))
      .rejects.toThrow(/终态结果不可用/);
  });
});
