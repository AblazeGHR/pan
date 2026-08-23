// @vitest-environment jsdom
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';

describe('appSettingsStore', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
  });

  it('starts with defaults when nothing is persisted', () => {
    const s = useAppSettingsStore.getState();
    expect(s.defaultGroupBy).toBe('none');
    expect(s.showMetaAgent).toBe(true);
    expect(s.showTaskAgent).toBe(true);
    expect(s.showQQ).toBe(true);
  });

  it('persists the whole settings object to localStorage on change', () => {
    useAppSettingsStore.getState().setDefaultGroupBy('workdir');
    useAppSettingsStore.getState().setShowMetaAgent(false);
    useAppSettingsStore.getState().setShowTaskAgent(false);
    useAppSettingsStore.getState().setShowQQ(false);

    expect(useAppSettingsStore.getState().defaultGroupBy).toBe('workdir');
    const raw = localStorage.getItem('pan:appSettings');
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw!)).toEqual({
      defaultGroupBy: 'workdir',
      showMetaAgent: false,
      showTaskAgent: false,
      showQQ: false,
    });
  });

  it('validates persisted values on load, falling back to defaults', async () => {
    localStorage.setItem(
      'pan:appSettings',
      JSON.stringify({
        defaultGroupBy: 'bogus',
        showMetaAgent: 'yes',
        showTaskAgent: false,
        showQQ: true,
      }),
    );
    vi.resetModules();
    const mod = await import('@/stores/appSettingsStore');
    const s = mod.useAppSettingsStore.getState();
    expect(s.defaultGroupBy).toBe('none');
    expect(s.showMetaAgent).toBe(true);
    expect(s.showTaskAgent).toBe(false);
    expect(s.showQQ).toBe(true);
  });

  it('resets all settings to defaults and persists them', () => {
    useAppSettingsStore.getState().setDefaultGroupBy('manager');
    useAppSettingsStore.getState().setShowMetaAgent(false);
    useAppSettingsStore.getState().setShowQQ(false);

    useAppSettingsStore.getState().resetSettings();

    const s = useAppSettingsStore.getState();
    expect(s.defaultGroupBy).toBe(DEFAULT_SETTINGS.defaultGroupBy);
    expect(s.showMetaAgent).toBe(DEFAULT_SETTINGS.showMetaAgent);
    expect(s.showTaskAgent).toBe(DEFAULT_SETTINGS.showTaskAgent);
    expect(s.showQQ).toBe(DEFAULT_SETTINGS.showQQ);
    expect(JSON.parse(localStorage.getItem('pan:appSettings')!)).toEqual(
      DEFAULT_SETTINGS,
    );
  });
});
