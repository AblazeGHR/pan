// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  capabilityAvailable,
  resetPlatformStoreForTests,
  usePlatformStore,
} from './platformStore';
import {
  capabilityAllowed,
  platformLabel,
  sanitizeCapabilities,
} from '@/utils/platformCapabilities';

const { fetchPlatformInfoMock } = vi.hoisted(() => ({ fetchPlatformInfoMock: vi.fn() }));

vi.mock('@/services/api', () => ({ fetchPlatformInfo: fetchPlatformInfoMock }));

describe('platform capability store', () => {
  beforeEach(() => {
    fetchPlatformInfoMock.mockReset();
    resetPlatformStoreForTests();
  });

  it('records the platform and capabilities from the endpoint', async () => {
    fetchPlatformInfoMock.mockResolvedValue({
      platform: 'darwin',
      capabilities: { 'terminal.native': false, rewind: false, 'service.lifecycle': false },
    });
    await usePlatformStore.getState().load();
    const state = usePlatformStore.getState();
    expect(state.platform).toBe('darwin');
    expect(state.loaded).toBe(true);
    expect(capabilityAvailable('terminal.native')).toBe(false);
    expect(capabilityAvailable('rewind')).toBe(false);
    expect(capabilityAvailable('service.lifecycle')).toBe(false);
  });

  it('leaves every capability available when the endpoint is missing', async () => {
    fetchPlatformInfoMock.mockRejectedValue(new Error('HTTP 404: Not Found'));
    await usePlatformStore.getState().load();
    const state = usePlatformStore.getState();
    expect(state.loaded).toBe(true);
    expect(state.platform).toBeNull();
    expect(state.capabilities).toBeNull();
    for (const capability of ['terminal.native', 'rewind', 'service.lifecycle', 'terminal.detach']) {
      expect(capabilityAvailable(capability)).toBe(true);
    }
  });

  it('degrades safely on a malformed body', async () => {
    fetchPlatformInfoMock.mockResolvedValue({ platform: 'linux', capabilities: 'nope' });
    await usePlatformStore.getState().load();
    expect(usePlatformStore.getState().platform).toBe('linux');
    expect(usePlatformStore.getState().capabilities).toBeNull();
    expect(capabilityAvailable('terminal.native')).toBe(true);
  });

  it('probes once per page load', async () => {
    fetchPlatformInfoMock.mockResolvedValue({ platform: 'windows', capabilities: {} });
    await Promise.all([
      usePlatformStore.getState().load(),
      usePlatformStore.getState().load(),
    ]);
    await usePlatformStore.getState().load();
    expect(fetchPlatformInfoMock).toHaveBeenCalledTimes(1);
  });

  it('keeps the previous state when the probe throws synchronously', async () => {
    fetchPlatformInfoMock.mockImplementation(() => {
      throw new Error('service module unavailable');
    });
    await usePlatformStore.getState().load();
    expect(usePlatformStore.getState().capabilities).toBeNull();
    expect(capabilityAvailable('rewind')).toBe(true);
  });
});

describe('capability rules', () => {
  it('treats an explicit false as the only denial', () => {
    expect(capabilityAllowed({ rewind: false }, 'rewind')).toBe(false);
    expect(capabilityAllowed({ rewind: true }, 'rewind')).toBe(true);
    expect(capabilityAllowed({}, 'rewind')).toBe(true);
    expect(capabilityAllowed(null, 'rewind')).toBe(true);
    expect(capabilityAllowed(undefined, 'rewind')).toBe(true);
  });

  it('drops non-boolean entries instead of guessing', () => {
    expect(sanitizeCapabilities({ rewind: false, 'terminal.native': 'yes' }))
      .toEqual({ rewind: false });
    expect(sanitizeCapabilities({})).toBeNull();
    expect(sanitizeCapabilities([])).toBeNull();
    expect(sanitizeCapabilities(null)).toBeNull();
  });

  it('names the platform for unsupported copy', () => {
    expect(platformLabel('darwin')).toBe('macOS');
    expect(platformLabel('linux')).toBe('Linux');
    expect(platformLabel('windows')).toBe('Windows');
    expect(platformLabel(null)).toBe('当前平台');
  });
});
