// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import { ShortcutsSettings } from './ShortcutsSettings';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import { usePluginPanelStore } from '@/stores/pluginPanelStore';
import { useUIStore } from '@/stores/uiStore';
import { useGlobalShortcuts } from '@/shortcuts/useGlobalShortcuts';
import { updateUiSettings } from '@/services/api';

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return { ...actual, updateUiSettings: vi.fn(async () => ({})) };
});

const mockedUpdate = vi.mocked(updateUiSettings);

function DispatchHarness({ navigate }: { navigate: (to: string) => void }) {
  useGlobalShortcuts(navigate);
  return null;
}

const panel = (pluginId: string, id: string, title: string) => ({
  pluginId,
  id,
  title,
  renderer: 'account-switcher/v1' as const,
  path: `/plugins/${pluginId}/${id}`,
  actions: {},
});

function row(actionId: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(`[data-shortcut-action="${actionId}"]`);
  if (!element) throw new Error(`missing shortcut row ${actionId}`);
  return element;
}

beforeEach(() => {
  localStorage.clear();
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, shortcuts: {}, loaded: true });
  useUIStore.setState({ sidebarCollapsed: false });
  usePluginPanelStore.setState({
    panels: [],
    loading: false,
    loaded: true,
    error: null,
    load: vi.fn(async () => undefined),
  });
  mockedUpdate.mockReset().mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  document.body.innerHTML = '';
});

describe('ShortcutsSettings', () => {
  it('renders app, dynamic panel, queue and session actions with platform labels', () => {
    usePluginPanelStore.setState({ panels: [panel('alpha', 'one', 'Alpha One')] });
    render(<ShortcutsSettings />);
    expect(screen.getByTestId('shortcuts-settings')).toBeTruthy();
    expect(row('app.toggleSidebar').textContent).toContain('Ctrl+B');
    expect(row('panel:plugin:alpha/one').textContent).toContain('Not set');
    expect(row('queue.enqueueLocked').textContent).toContain('Not set');
    expect(screen.getByTestId('shortcuts-restore-all')).toBeTruthy();
  });

  it('records, edits, clears and restores one action', async () => {
    render(<ShortcutsSettings />);
    const target = row('app.toggleSidebar');
    fireEvent.click(within(target).getByLabelText('Record shortcut for Toggle sidebar'));
    expect(within(target).getByText('Press keys…')).toBeTruthy();
    fireEvent.keyDown(window, {
      key: 'b',
      code: 'KeyB',
      ctrlKey: true,
      shiftKey: true,
    });
    expect(useAppSettingsStore.getState().shortcuts['app.toggleSidebar'])
      .toBe('Mod+Shift+B');
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Saved'));
    expect(mockedUpdate).toHaveBeenLastCalledWith({
      shortcuts: { 'app.toggleSidebar': 'Mod+Shift+B' },
    });
    expect(within(row('app.toggleSidebar')).getByText('Ctrl+Shift+B')).toBeTruthy();

    fireEvent.click(within(row('app.toggleSidebar')).getByLabelText('Clear shortcut for Toggle sidebar'));
    expect(useAppSettingsStore.getState().shortcuts['app.toggleSidebar']).toBeNull();
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Saved'));

    fireEvent.click(within(row('app.toggleSidebar')).getByLabelText('Restore default shortcut for Toggle sidebar'));
    expect(useAppSettingsStore.getState().shortcuts).toEqual({});
    expect(within(row('app.toggleSidebar')).getByText('Ctrl+B')).toBeTruthy();
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Saved'));
  });

  it('lists the queue selection shortcuts with defaults and supports rebinding plus restore', async () => {
    render(<ShortcutsSettings />);
    expect(row('queue.selectPrevious').textContent).toContain('Alt+↑');
    expect(row('queue.selectNext').textContent).toContain('Alt+↓');
    expect(row('queue.editSelected').textContent).toContain('Alt+Enter');
    expect(row('queue.toggleSelectedLock').textContent).toContain('Alt+L');

    fireEvent.click(
      within(row('queue.selectNext')).getByLabelText('Record shortcut for Select next queued message'),
    );
    fireEvent.keyDown(window, { key: 'j', code: 'KeyJ', ctrlKey: true, altKey: true });
    expect(useAppSettingsStore.getState().shortcuts['queue.selectNext']).toBe('Mod+Alt+J');
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Saved'));
    expect(within(row('queue.selectNext')).getByText('Ctrl+Alt+J')).toBeTruthy();

    fireEvent.click(
      within(row('queue.selectNext')).getByLabelText('Restore default shortcut for Select next queued message'),
    );
    expect(useAppSettingsStore.getState().shortcuts['queue.selectNext']).toBeUndefined();
    expect(within(row('queue.selectNext')).getByText('Alt+↓')).toBeTruthy();
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Saved'));
  });

  it('records Ctrl+4 and function keys, cancels with Tab/Escape and reports unsupported typing', async () => {
    render(<ShortcutsSettings />);
    const record = () => fireEvent.click(within(row('composer.steer')).getByRole('button', { name: /Record shortcut/ }));
    record();
    fireEvent.keyDown(window, { key: '4', code: 'Digit4', ctrlKey: true });
    expect(useAppSettingsStore.getState().shortcuts['composer.steer']).toBe('Mod+4');
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Saved'));
    record();
    fireEvent.keyDown(window, { key: 'F8', code: 'F8' });
    expect(useAppSettingsStore.getState().shortcuts['composer.steer']).toBe('F8');
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Saved'));
    record();
    fireEvent.keyDown(window, { key: 'x', code: 'KeyX' });
    expect(screen.getByRole('alert').textContent).toContain('modifier');
    const tab = new KeyboardEvent('keydown', { key: 'Tab', cancelable: true });
    act(() => window.dispatchEvent(tab));
    expect(tab.defaultPrevented).toBe(false);
    expect(within(row('composer.steer')).queryByText('Press keys…')).toBeNull();
    record();
    fireEvent.keyDown(window, { key: 'Escape', code: 'Escape' });
    expect(within(row('composer.steer')).queryByText('Press keys…')).toBeNull();
  });

  it('rejects composer/global collisions and makes persistence failure visible', async () => {
    render(<ShortcutsSettings />);
    fireEvent.click(within(row('composer.steer')).getByRole('button', { name: /Record shortcut/ }));
    fireEvent.keyDown(window, { key: 'Enter', code: 'Enter', shiftKey: true });
    expect(screen.getByRole('alert').textContent).toContain('Insert newline');
    mockedUpdate.mockRejectedValueOnce(new Error('offline'));
    fireEvent.click(within(row('composer.steer')).getByRole('button', { name: /Record shortcut/ }));
    fireEvent.keyDown(window, { key: '4', code: 'Digit4', ctrlKey: true });
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Could not save'));
    expect(useAppSettingsStore.getState().shortcuts['composer.steer']).toBeUndefined();
  });

  it('does not run a business action while recording, including its own default', () => {
    const navigate = vi.fn();
    render(<><DispatchHarness navigate={navigate} /><ShortcutsSettings /></>);
    fireEvent.click(within(row('app.toggleSidebar')).getByLabelText('Record shortcut for Toggle sidebar'));
    fireEvent.keyDown(window, { key: 'b', code: 'KeyB', ctrlKey: true });
    expect(useAppSettingsStore.getState().shortcuts['app.toggleSidebar']).toBe('Mod+B');
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
  });

  it('rejects reserved and duplicate combinations with an actionable error', () => {
    render(<ShortcutsSettings />);
    fireEvent.click(within(row('app.toggleSidebar')).getByLabelText('Record shortcut for Toggle sidebar'));
    fireEvent.keyDown(window, { key: 'r', code: 'KeyR', ctrlKey: true });
    expect(screen.getByTestId('shortcut-recorder-error').textContent).toContain('reserved');
    expect(useAppSettingsStore.getState().shortcuts).toEqual({});

    fireEvent.click(within(row('app.toggleSidebar')).getByLabelText('Record shortcut for Toggle sidebar'));
    fireEvent.keyDown(window, { key: 'k', code: 'KeyK', ctrlKey: true });
    expect(screen.getByTestId('shortcut-recorder-error').textContent)
      .toContain('Open command palette');
    expect(useAppSettingsStore.getState().shortcuts).toEqual({});
  });

  it('shows persisted conflicts without executing either action', () => {
    useAppSettingsStore.setState({ shortcuts: { 'app.commandPalette': 'Mod+B' } });
    const navigate = vi.fn();
    render(<><DispatchHarness navigate={navigate} /><ShortcutsSettings /></>);
    expect(within(row('app.toggleSidebar')).getByTestId('shortcut-conflict')).toBeTruthy();
    fireEvent.keyDown(document, { key: 'b', code: 'KeyB', ctrlKey: true });
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
  });

  it('updates panel actions when the registry adds or removes panels', () => {
    useAppSettingsStore.setState({
      shortcuts: {
        'panel:plugin:alpha/one': 'Mod+3',
        'panel:plugin:beta/two': 'Mod+4',
      },
    });
    usePluginPanelStore.setState({
      panels: [panel('alpha', 'one', 'Alpha One'), panel('beta', 'two', 'Beta Two')],
    });
    render(<ShortcutsSettings />);
    expect(row('panel:plugin:alpha/one').textContent).toContain('Ctrl+3');
    expect(row('panel:plugin:beta/two').textContent).toContain('Ctrl+4');

    act(() => {
      usePluginPanelStore.setState({ panels: [panel('beta', 'two', 'Beta Two')] });
    });
    expect(document.querySelector('[data-shortcut-action="panel:plugin:alpha/one"]')).toBeNull();
    expect(row('panel:plugin:beta/two').textContent).toContain('Ctrl+4');
    expect(useAppSettingsStore.getState().shortcuts['panel:plugin:alpha/one'])
      .toBe('Mod+3');
  });

  it('keeps recording while an IME keydown arrives and cancels on Escape', () => {
    render(<ShortcutsSettings />);
    const target = row('app.toggleSidebar');
    fireEvent.click(within(target).getByLabelText('Record shortcut for Toggle sidebar'));
    fireEvent.keyDown(window, {
      key: 'b',
      code: 'KeyB',
      ctrlKey: true,
      isComposing: true,
    });
    expect(useAppSettingsStore.getState().shortcuts).toEqual({});
    expect(within(target).getByText('Press keys…')).toBeTruthy();
    fireEvent.keyDown(window, { key: 'Escape', code: 'Escape' });
    expect(within(row('app.toggleSidebar')).getByText('Ctrl+B')).toBeTruthy();
  });
});
