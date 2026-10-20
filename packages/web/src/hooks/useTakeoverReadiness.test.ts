// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { fetchTakeoverReadiness } from '@/services/api';
import { useTakeoverReadiness } from './useTakeoverReadiness';
import type { Session } from '@/types';

vi.mock('@/services/api', () => ({ fetchTakeoverReadiness: vi.fn() }));
afterEach(() => vi.resetAllMocks());
const session = (id: string): Session => ({ id, name: id, history: [], alwaysThinkingEnabled: false, effort: '' });

it('unlocks after native establishment without using assistant history count', async () => {
  vi.mocked(fetchTakeoverReadiness).mockResolvedValueOnce({ ready: false, reason: 'No CLI resume data' })
    .mockResolvedValueOnce({ ready: true, reason: null });
  const { result, rerender } = renderHook(({ s }) => useTakeoverReadiness(s), { initialProps: { s: session('A') } });
  await waitFor(() => expect(result.current.reason).toBe('No CLI resume data'));
  rerender({ s: { ...session('A'), cliSessionId: 'real' } });
  await waitFor(() => expect(result.current.ready).toBe(true));
});

it('ignores a late readiness response from the previous selected Session', async () => {
  let finish!: (value: { ready: boolean; reason: string | null }) => void;
  vi.mocked(fetchTakeoverReadiness).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValueOnce({ ready: false, reason: 'B has no CLI session' });
  const { result, rerender } = renderHook(({ s }) => useTakeoverReadiness(s), { initialProps: { s: session('A') } });
  rerender({ s: session('B') });
  await waitFor(() => expect(result.current.reason).toBe('B has no CLI session'));
  await act(async () => finish({ ready: true, reason: null }));
  expect(result.current.ready).toBe(false);
});
