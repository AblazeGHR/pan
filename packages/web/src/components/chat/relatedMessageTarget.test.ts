import { afterEach, expect, it, vi } from 'vitest';
import { resolveRelatedMessageTarget } from './relatedMessageTarget';
import { fetchSessionNavigation } from '@/services/api';
vi.mock('@/services/api', () => ({ fetchSessionNavigation: vi.fn() }));
afterEach(() => vi.resetAllMocks());

it('loads older compact pages by canonical before offset', async () => {
  const history = Array.from({ length: 500 }, () => ({ role: 'assistant', content: '' }));
  vi.mocked(fetchSessionNavigation).mockResolvedValueOnce({ history, start: 500, total: 1000, hasMore: true, historyEpoch: 'a' })
    .mockResolvedValueOnce({ history: [{ role: 'user', content: 'Old', messageId: 'target' }, ...history.slice(1)], start: 0, total: 1000, hasMore: false, historyEpoch: 'a' });
  expect(await resolveRelatedMessageTarget('owner', 'target')).toEqual({ offset: 0, total: 1000, messageId: 'target' });
  expect(fetchSessionNavigation).toHaveBeenNthCalledWith(2, 'owner', 500, 500, undefined);
});

it('fails closed on uncovered/replaced/non-user targets', async () => {
  vi.mocked(fetchSessionNavigation).mockResolvedValue({ history: [], start: 0, total: 1, hasMore: false });
  await expect(resolveRelatedMessageTarget('owner', 'target')).rejects.toThrow('未覆盖');
  vi.mocked(fetchSessionNavigation).mockResolvedValue({ history: [], start: 0, total: 0, hasMore: false });
  await expect(resolveRelatedMessageTarget('owner', 'target')).rejects.toThrow('删除');
  vi.mocked(fetchSessionNavigation).mockResolvedValue({ history: [{ role: 'user', source: 'agent', content: 'Assign', messageId: 'target' }], start: 0, total: 1, hasMore: false });
  await expect(resolveRelatedMessageTarget('owner', 'target')).rejects.toThrow('user');
});
