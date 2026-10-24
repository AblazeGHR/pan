// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LocalPluginPanelsSettings } from './LocalPluginPanelsSettings';
import {
  buildLocalPluginPanel, fetchLocalPluginPanels, fetchPluginPanels,
  setLocalPluginPanelEnabled, unbuildLocalPluginPanel,
} from '@/services/api';
import type { LocalPluginPanel } from '@/types/pluginPanels';

vi.mock('@/services/api', () => ({
  buildLocalPluginPanel: vi.fn(),
  fetchLocalPluginPanels: vi.fn(),
  fetchPluginPanels: vi.fn(),
  setLocalPluginPanelEnabled: vi.fn(),
  unbuildLocalPluginPanel: vi.fn(),
}));

const panel = (patch: Partial<LocalPluginPanel> = {}): LocalPluginPanel => ({
  pluginId: 'local-console', panelId: 'console', title: 'Local Console', entry: 'index.html',
  assetPath: '/panel-assets/local-console/console/', enabled: false, status: 'unbuilt', available: false,
  activeBuild: null, builtAt: null, startedAt: null, finishedAt: null, lastError: null, log: '', ...patch,
});

let current: LocalPluginPanel;

beforeEach(() => {
  current = panel();
  vi.mocked(fetchLocalPluginPanels).mockReset().mockImplementation(async () => [structuredClone(current)]);
  vi.mocked(fetchPluginPanels).mockReset().mockResolvedValue([]);
  vi.mocked(buildLocalPluginPanel).mockReset();
  vi.mocked(setLocalPluginPanelEnabled).mockReset();
  vi.mocked(unbuildLocalPluginPanel).mockReset();
});

afterEach(() => cleanup());

describe('LocalPluginPanelsSettings', () => {
  it('requires a successful build before enabling and keeps lifecycle buttons in sync', async () => {
    const view = render(<LocalPluginPanelsSettings />);
    await screen.findByText('Not built');
    const enable = screen.getByRole('button', { name: 'Enable' }) as HTMLButtonElement;
    expect(enable.disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Build' })).toBeTruthy();

    vi.mocked(buildLocalPluginPanel).mockImplementation(async () => {
      current = panel({ status: 'built', available: true, activeBuild: 'b1', builtAt: Date.now(), log: 'built' });
      return structuredClone(current);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Build' }));
    await screen.findByText('Built');
    expect(buildLocalPluginPanel).toHaveBeenCalledWith('local-console', 'console');
    expect((screen.getByRole('button', { name: 'Enable' }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByRole('button', { name: 'Rebuild' })).toBeTruthy();
    expect(screen.getByText('built')).toBeTruthy();

    vi.mocked(setLocalPluginPanelEnabled).mockImplementation(async () => {
      current = panel({ status: 'built', available: true, enabled: true, activeBuild: 'b1', builtAt: Date.now() });
      return structuredClone(current);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Enable' }));
    await screen.findByText('Enabled');
    expect(setLocalPluginPanelEnabled).toHaveBeenCalledWith('local-console', 'console', true);
    expect(fetchPluginPanels).toHaveBeenCalled();
    view.unmount();
  });

  it('reports a failed rebuild while the previous build stays enabled', async () => {
    current = panel({ status: 'failed', available: true, enabled: true, activeBuild: 'b1',
      lastError: 'Build command exited with code 3.', log: 'intentional build failure\n' });
    render(<LocalPluginPanelsSettings />);
    await screen.findByText('Build failed · previous build kept');
    expect(screen.getByRole('button', { name: 'Disable' })).toBeTruthy();
    expect(screen.getByText('Build command exited with code 3.')).toBeTruthy();
    expect(screen.getByText(/intentional build failure/)).toBeTruthy();

    vi.mocked(unbuildLocalPluginPanel).mockImplementation(async () => {
      current = panel();
      return structuredClone(current);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Unbuild' }));
    await screen.findByText('Not built');
    await waitFor(() => expect(unbuildLocalPluginPanel).toHaveBeenCalledWith('local-console', 'console'));
  });

  it('surfaces build progress and disables every mutation while building', async () => {
    current = panel({ status: 'building', log: 'building assets\n' });
    render(<LocalPluginPanelsSettings />);
    await screen.findByText('Building…');
    expect(screen.getByRole('status').textContent).toContain('Build in progress');
    for (const name of ['Build', 'Enable', 'Disable', 'Unbuild']) {
      expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true);
    }
  });
});
