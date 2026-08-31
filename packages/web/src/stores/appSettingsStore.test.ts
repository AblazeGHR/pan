// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  useAppSettingsStore,
  DEFAULT_SETTINGS,
  sanitizeSettings,
} from '@/stores/appSettingsStore';
import { fetchUiSettings, updateUiSettings } from '@/services/api';

vi.mock('@/services/api', () => ({
  fetchUiSettings: vi.fn(async () => ({})),
  updateUiSettings: vi.fn(async () => ({})),
}));

const mockedFetch = vi.mocked(fetchUiSettings);
const mockedUpdate = vi.mocked(updateUiSettings);

describe('appSettingsStore', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: false });
    mockedFetch.mockReset();
    mockedUpdate.mockReset();
    mockedFetch.mockResolvedValue({ ...DEFAULT_SETTINGS });
    mockedUpdate.mockResolvedValue({});
  });

  it('starts with defaults before the backend load completes', () => {
    const s = useAppSettingsStore.getState();
    expect(s.loaded).toBe(false);
    expect(s.defaultGroupBy).toBe('none');
    expect(s.showMetaAgent).toBe(true);
    expect(s.showTaskAgent).toBe(true);
    expect(s.showQQ).toBe(true);
    expect(s.showCodexTerminalInput).toBe(false);
  });

  it('applies backend ui settings on load', async () => {
    mockedFetch.mockResolvedValue({
      defaultGroupBy: 'workdir',
      showMetaAgent: false,
      showTaskAgent: true,
      showQQ: false,
    });

    await useAppSettingsStore.getState().loadSettings();

    const s = useAppSettingsStore.getState();
    expect(s.loaded).toBe(true);
    expect(s.defaultGroupBy).toBe('workdir');
    expect(s.showMetaAgent).toBe(false);
    expect(s.showTaskAgent).toBe(true);
    expect(s.showQQ).toBe(false);
  });

  it('validates server values on load, falling back to defaults', async () => {
    mockedFetch.mockResolvedValue({
      defaultGroupBy: 'bogus',
      showMetaAgent: 'yes',
      showTaskAgent: false,
      showQQ: true,
    });

    await useAppSettingsStore.getState().loadSettings();

    const s = useAppSettingsStore.getState();
    expect(s.defaultGroupBy).toBe('none');
    expect(s.showMetaAgent).toBe(true);
    expect(s.showTaskAgent).toBe(false);
    expect(s.showQQ).toBe(true);
  });

  it('keeps defaults when the backend load fails', async () => {
    mockedFetch.mockRejectedValue(new Error('network down'));

    await useAppSettingsStore.getState().loadSettings();

    const s = useAppSettingsStore.getState();
    expect(s.loaded).toBe(true);
    expect(s.defaultGroupBy).toBe('none');
    expect(s.showMetaAgent).toBe(true);
    expect(s.showTaskAgent).toBe(true);
    expect(s.showQQ).toBe(true);
  });

  it('writes each change back to the backend (PUT)', () => {
    useAppSettingsStore.getState().setDefaultGroupBy('workdir');
    useAppSettingsStore.getState().setShowMetaAgent(false);
    useAppSettingsStore.getState().setShowTaskAgent(false);
    useAppSettingsStore.getState().setShowQQ(false);
    useAppSettingsStore.getState().setCodexWarningToast(false);
    useAppSettingsStore.getState().setShowCodexTerminalInput(true);

    expect(useAppSettingsStore.getState().defaultGroupBy).toBe('workdir');
    expect(mockedUpdate).toHaveBeenNthCalledWith(1, { defaultGroupBy: 'workdir' });
    expect(mockedUpdate).toHaveBeenNthCalledWith(2, { showMetaAgent: false });
    expect(mockedUpdate).toHaveBeenNthCalledWith(3, { showTaskAgent: false });
    expect(mockedUpdate).toHaveBeenNthCalledWith(4, { showQQ: false });
    expect(mockedUpdate).toHaveBeenNthCalledWith(5, {
      notifications: { codexWarningToast: false },
    });
    expect(mockedUpdate).toHaveBeenNthCalledWith(6, { showCodexTerminalInput: true });
  });

  it('resets all settings to defaults and writes them back', () => {
    useAppSettingsStore.getState().setDefaultGroupBy('manager');
    useAppSettingsStore.getState().setShowMetaAgent(false);
    useAppSettingsStore.getState().setShowQQ(false);

    useAppSettingsStore.getState().resetSettings();

    const s = useAppSettingsStore.getState();
    expect(s.defaultGroupBy).toBe(DEFAULT_SETTINGS.defaultGroupBy);
    expect(s.showMetaAgent).toBe(DEFAULT_SETTINGS.showMetaAgent);
    expect(s.showTaskAgent).toBe(DEFAULT_SETTINGS.showTaskAgent);
    expect(s.showQQ).toBe(DEFAULT_SETTINGS.showQQ);
    expect(s.showCodexTerminalInput).toBe(DEFAULT_SETTINGS.showCodexTerminalInput);
    expect(mockedUpdate).toHaveBeenLastCalledWith({ ...DEFAULT_SETTINGS });
  });

  it('does not overwrite a change made while the initial load is in flight', async () => {
    let resolveLoad!: (v: Record<string, unknown>) => void;
    mockedFetch.mockReturnValue(
      new Promise((res) => {
        resolveLoad = res;
      }),
    );

    const loadPromise = useAppSettingsStore.getState().loadSettings();
    // User toggles a switch before the GET resolves.
    useAppSettingsStore.getState().setShowMetaAgent(false);
    // The GET resolves with the pre-change (stale) server value.
    resolveLoad({
      defaultGroupBy: 'none',
      showMetaAgent: true,
      showTaskAgent: true,
      showQQ: true,
    });

    await loadPromise;

    expect(useAppSettingsStore.getState().showMetaAgent).toBe(false);
  });

  it('sanitizeSettings fills missing fields with defaults', () => {
    expect(sanitizeSettings({ showQQ: false })).toEqual({
      ...DEFAULT_SETTINGS,
      showQQ: false,
    });
    expect(
      sanitizeSettings({ notifications: { codexWarningToast: false } }).notifications,
    ).toEqual({ codexWarningToast: false });
    expect(sanitizeSettings(null)).toEqual({ ...DEFAULT_SETTINGS });
    expect(sanitizeSettings({ showCodexTerminalInput: 'yes' }).showCodexTerminalInput)
      .toBe(false);
    expect(sanitizeSettings({ showCodexTerminalInput: true }).showCodexTerminalInput)
      .toBe(true);
  });
});
