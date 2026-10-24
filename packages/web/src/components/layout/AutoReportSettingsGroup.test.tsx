// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AutoReportSettingsGroup } from './AutoReportSettingsGroup';
import { DEFAULT_AUTO_REPORT, DEFAULT_AUTO_REPORT_SUMMARY, DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import { fetchPluginMessageSources, updateUiSettings } from '@/services/api';

vi.mock('@/services/api', () => ({
  fetchPluginMessageSources: vi.fn(), fetchUiSettings: vi.fn(), updateUiSettings: vi.fn(),
}));

describe('Auto report settings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
    vi.mocked(fetchPluginMessageSources).mockResolvedValue({ sources: [
      { id: 'new-provider', label: 'New provider', defaultEnabled: false, effectiveEnabled: false, effectiveSummaryEnabled: false },
      { id: 'declared-on', label: 'Declared on', defaultEnabled: true, effectiveEnabled: true, effectiveSummaryEnabled: false },
    ] });
    vi.mocked(updateUiSettings).mockImplementation(async (patch) => ({ ...patch }));
  });
  afterEach(cleanup);

  it('renders dynamic message providers, preserves independent overrides and resets declaration defaults', async () => {
    render(<AutoReportSettingsGroup />);
    const provider = await screen.findByRole('checkbox', { name: 'Auto report plugin New provider' }) as HTMLInputElement;
    const declared = screen.getByRole('checkbox', { name: 'Auto report plugin Declared on' }) as HTMLInputElement;
    expect(provider.checked).toBe(false);
    expect(declared.checked).toBe(true);
    expect((screen.getByRole('checkbox', { name: 'Auto report MA' }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('checkbox', { name: 'Auto report System' }) as HTMLInputElement).checked).toBe(false);
    expect((screen.getByRole('checkbox', { name: 'Auto report Real user' }) as HTMLInputElement).checked).toBe(false);
    fireEvent.click(provider);
    await waitFor(() => expect(provider.checked).toBe(true));
    expect(declared.checked).toBe(true);
    fireEvent.click(declared);
    await waitFor(() => expect(declared.checked).toBe(false));
    expect(provider.checked).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Reset auto report defaults' }));
    await waitFor(() => expect(provider.checked).toBe(false));
    expect(declared.checked).toBe(true);
    expect(updateUiSettings).toHaveBeenLastCalledWith({ autoReport: DEFAULT_AUTO_REPORT });
  });

  it('keeps prompt summaries off by default and independent from answer reporting', async () => {
    render(<AutoReportSettingsGroup />);
    await screen.findByRole('region', { name: 'Prompt summary' });
    const summaryMa = screen.getByRole('checkbox', { name: 'Prompt summary MA' }) as HTMLInputElement;
    const summarySystem = screen.getByRole('checkbox', { name: 'Prompt summary System' }) as HTMLInputElement;
    const summaryUser = screen.getByRole('checkbox', { name: 'Prompt summary Real user' }) as HTMLInputElement;
    const declaredSummary = screen.getByRole('checkbox', { name: 'Prompt summary plugin Declared on' }) as HTMLInputElement;
    expect([summaryMa.checked, summarySystem.checked, summaryUser.checked, declaredSummary.checked])
      .toEqual([false, false, false, false]);
    fireEvent.click(summaryMa);
    await waitFor(() => expect(summaryMa.checked).toBe(true));
    // Answer reporting is untouched by a summary switch.
    expect((screen.getByRole('checkbox', { name: 'Auto report MA' }) as HTMLInputElement).checked).toBe(true);
    expect(useAppSettingsStore.getState().autoReport.summary).toEqual({
      ...DEFAULT_AUTO_REPORT_SUMMARY, ma: true, plugins: {},
    });
    expect(updateUiSettings).toHaveBeenLastCalledWith({
      autoReport: { ...DEFAULT_AUTO_REPORT, summary: { ...DEFAULT_AUTO_REPORT_SUMMARY, ma: true, plugins: {} } },
    });
    // The other policy's reset never clears the summary switch.
    fireEvent.click(screen.getByRole('button', { name: 'Reset auto report defaults' }));
    await waitFor(() => expect(useAppSettingsStore.getState().autoReport.summary.ma).toBe(true));
    fireEvent.click(declaredSummary);
    await waitFor(() => expect(declaredSummary.checked).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Reset prompt summary defaults' }));
    await waitFor(() => expect(summaryMa.checked).toBe(false));
    expect(declaredSummary.checked).toBe(false);
    expect((screen.getByRole('checkbox', { name: 'Auto report plugin Declared on' }) as HTMLInputElement).checked).toBe(true);
    expect(useAppSettingsStore.getState().autoReport.summary).toEqual(DEFAULT_AUTO_REPORT_SUMMARY);
  });

  it('does not claim an unsaved value after a failed PUT and exposes load failures', async () => {
    vi.mocked(updateUiSettings).mockRejectedValue(new Error('write failed'));
    render(<AutoReportSettingsGroup />);
    await screen.findByRole('checkbox', { name: 'Auto report plugin New provider' });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Auto report MA' }));
    await screen.findByRole('alert');
    expect((screen.getByRole('checkbox', { name: 'Auto report MA' }) as HTMLInputElement).checked).toBe(true);
    cleanup();
    vi.mocked(fetchPluginMessageSources).mockRejectedValue(new Error('registry unavailable'));
    render(<AutoReportSettingsGroup />);
    expect((await screen.findByRole('alert')).textContent).toContain('registry unavailable');
  });

  it('ignores a registry result after unmount', async () => {
    let resolve!: (data: Awaited<ReturnType<typeof fetchPluginMessageSources>>) => void;
    vi.mocked(fetchPluginMessageSources).mockReturnValue(new Promise((done) => { resolve = done; }));
    const view = render(<AutoReportSettingsGroup />);
    view.unmount();
    await act(async () => { resolve({ sources: [] }); });
    expect(screen.queryByRole('region', { name: 'Auto report' })).toBeNull();
  });
});
