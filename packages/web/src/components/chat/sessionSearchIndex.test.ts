import { describe, expect, it, vi } from 'vitest';
import { scanSessionHistory, StaleSessionHistoryError, type HistoryPageLoader } from './sessionSearchIndex';
import type { ApiSessionHistoryResponse } from '@/types';

const page = (
  history: ApiSessionHistoryResponse['history'],
  total: number,
  start: number,
  historyRevision = 3,
): ApiSessionHistoryResponse => ({ history, total, start, hasMore: start > 0, historyEpoch: 'epoch-a', historyRevision });

describe('scanSessionHistory', () => {
  it('scans every page, filters non-body and legacy rows, and counts each persistent message once', async () => {
    const loader = vi.fn<HistoryPageLoader>()
      .mockResolvedValueOnce(page([
        { role: 'assistant', content: 'older match and match again', messageId: 'a-1' },
        { role: 'thinking', content: 'match private reasoning', messageId: 't-1' },
      ], 4, 2))
      .mockResolvedValueOnce(page([
        { role: 'user', content: '@@@@by qq: match in QQ body', messageId: 'u-1' },
        { role: 'assistant', content: 'match without durable id', messageId: 'legacy:s1:epoch-a:0' },
      ], 4, 0));

    const result = await scanSessionHistory(loader, 's1', 'MATCH', { pageSize: 2 });

    expect(loader.mock.calls.map(([sid, before, limit]) => [sid, before, limit])).toEqual([
      ['s1', 0, 2], ['s1', 2, 2],
    ]);
    expect(result.total).toBe(4);
    expect(result.hits).toEqual([
      { messageId: 'u-1', messageIndex: 0, role: 'user', snippet: '@@@@by qq: match in QQ body' },
      { messageId: 'a-1', messageIndex: 2, role: 'assistant', snippet: 'older match and match again' },
    ]);
  });

  it('checks page revisions, total, and the session summary snapshot for stale scans', async () => {
    const loader: HistoryPageLoader = vi.fn()
      .mockResolvedValueOnce(page([{ role: 'user', content: 'needle', messageId: 'u-1' }], 2, 1, 3))
      .mockResolvedValueOnce(page([{ role: 'assistant', content: 'needle', messageId: 'a-1' }], 3, 0, 4));

    await expect(scanSessionHistory(loader, 's1', 'needle', { pageSize: 1 }))
      .rejects.toBeInstanceOf(StaleSessionHistoryError);
    const snapshotLoader: HistoryPageLoader = vi.fn().mockResolvedValue(
      page([{ role: 'user', content: 'needle', messageId: 'u-1' }], 2, 0, 3),
    );
    await expect(scanSessionHistory(snapshotLoader, 's1', 'needle', {
      expectedVersion: { total: 99, historyRevision: 3 },
    })).rejects.toBeInstanceOf(StaleSessionHistoryError);
  });

  it('honors AbortSignal between requests and after an in-flight page resolves', async () => {
    const controller = new AbortController();
    const loader = vi.fn<HistoryPageLoader>(async (_sid, _before, _limit, signal) => {
      expect(signal).toBe(controller.signal);
      controller.abort();
      return page([{ role: 'user', content: 'needle', messageId: 'u-1' }], 1, 0);
    });

    await expect(scanSessionHistory(loader, 's1', 'needle', { signal: controller.signal }))
      .rejects.toMatchObject({ name: 'AbortError' });
    expect(loader).toHaveBeenCalledTimes(1);
  });
});
