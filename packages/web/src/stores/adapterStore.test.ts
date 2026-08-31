import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAdapterStore } from './adapterStore';
import type { ApiCliStatusResponse } from '@/types';

const apiMock = vi.hoisted(() => ({
  fetchAdapterConfig: vi.fn(),
  fetchAdapters: vi.fn(),
  fetchCliStatus: vi.fn(),
  patchSession: vi.fn(),
  workerSettings: vi.fn(),
}));

vi.mock('@/services/api', () => apiMock);

const status: ApiCliStatusResponse = {
  adapters: [{
    name: 'codex',
    label: 'Codex',
    available: true,
    command: ['codex'],
    missing: [],
    hint: '',
  }],
  available: ['codex'],
  hasAvailable: true,
};

describe('adapterStore CLI status', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAdapterStore.setState({
      adapters: [],
      cliStatus: null,
      cliStatusLoading: false,
      cliStatusError: null,
    });
  });

  it('stores the CLI availability response and exposes only available diagnostics', async () => {
    apiMock.fetchCliStatus.mockResolvedValueOnce(status);

    await useAdapterStore.getState().loadCliStatus();

    expect(useAdapterStore.getState().cliStatus).toBe(status);
    expect(useAdapterStore.getState().cliStatusError).toBeNull();
    expect(useAdapterStore.getState().cliStatusLoading).toBe(false);
  });

  it('keeps the error explicit when CLI status cannot be loaded', async () => {
    apiMock.fetchCliStatus.mockRejectedValueOnce(new Error('network down'));

    await useAdapterStore.getState().loadCliStatus();

    expect(useAdapterStore.getState().cliStatus).toBeNull();
    expect(useAdapterStore.getState().cliStatusError).toBe('network down');
    expect(useAdapterStore.getState().cliStatusLoading).toBe(false);
  });

  it('does not fabricate cbc when the registered adapter list request fails', async () => {
    apiMock.fetchAdapters.mockRejectedValueOnce(new Error('network down'));

    await useAdapterStore.getState().loadAdapterList();

    expect(useAdapterStore.getState().adapters).toEqual([]);
  });
});
