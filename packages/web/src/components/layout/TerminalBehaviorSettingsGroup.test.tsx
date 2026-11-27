// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TerminalBehaviorSettingsGroup } from './TerminalBehaviorSettingsGroup';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import { fetchRuntimeIssueSchema, updateUiSettings } from '@/services/api';

vi.mock('@/services/api', () => ({
  updateUiSettings: vi.fn(),
  fetchRuntimeIssueSchema: vi.fn(),
}));

const CLASSIFICATIONS = [
  'codex_quota_exhausted',
  'codex_capacity',
  'cbc_rate_limited',
  'provider_rate_limited',
  'unknown',
];

describe('Terminal behavior settings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, terminalBehavior: { autoReportByErrorType: {} } });
    vi.mocked(updateUiSettings).mockImplementation(async (patch) => patch);
    vi.mocked(fetchRuntimeIssueSchema).mockResolvedValue({
      version: 1,
      eventType: 'runtime.issue.v1',
      classification: CLASSIFICATIONS,
    });
  });
  afterEach(cleanup);

  it('defaults unknown classifications to report and persists an explicit skip', async () => {
    render(<TerminalBehaviorSettingsGroup />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Runtime error classification' }), { target: { value: 'provider_rate_limited' } });
    fireEvent.click(screen.getByRole('button', { name: 'Disable report' }));
    await waitFor(() => expect(useAppSettingsStore.getState().terminalBehavior.autoReportByErrorType.provider_rate_limited).toBe(false));
    expect(updateUiSettings).toHaveBeenCalledWith({ terminalBehavior: { autoReportByErrorType: { provider_rate_limited: false } } });
  });

  it('keeps the saved value and shows an error when persistence fails', async () => {
    vi.mocked(updateUiSettings).mockRejectedValue(new Error('write failed'));
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, terminalBehavior: { autoReportByErrorType: { unknown: true } } });
    render(<TerminalBehaviorSettingsGroup />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Runtime error classification' }), { target: { value: 'provider_rate_limited' } });
    fireEvent.click(screen.getByRole('button', { name: 'Disable report' }));
    expect((await screen.findByRole('alert')).textContent).toContain('write failed');
    expect(useAppSettingsStore.getState().terminalBehavior.autoReportByErrorType).toEqual({ unknown: true });
  });

  it('searches partial input across quota and rate-limit classifications', async () => {
    render(<TerminalBehaviorSettingsGroup />);
    const input = screen.getByRole('combobox', { name: 'Runtime error classification' });
    fireEvent.change(input, { target: { value: 'limit' } });
    expect(await screen.findByRole('option', { name: 'provider_rate_limited' })).toBeTruthy();
    const options = screen.getAllByRole('option').map((option) => option.textContent);
    expect(options).toEqual(expect.arrayContaining([
      'cbc_rate_limited',
      'provider_rate_limited',
      'codex_quota_exhausted',
    ]));
    expect(options).not.toContain('unknown');
    expect(updateUiSettings).not.toHaveBeenCalled();
  });

  it('fills the input when a suggestion is clicked without saving immediately', async () => {
    render(<TerminalBehaviorSettingsGroup />);
    const input = screen.getByRole('combobox', { name: 'Runtime error classification' });
    fireEvent.change(input, { target: { value: 'limit' } });
    fireEvent.click(await screen.findByRole('option', { name: 'provider_rate_limited' }));
    expect((input as HTMLInputElement).value).toBe('provider_rate_limited');
    expect(updateUiSettings).not.toHaveBeenCalled();
    expect(useAppSettingsStore.getState().terminalBehavior.autoReportByErrorType).toEqual({});

    fireEvent.click(screen.getByRole('button', { name: 'Disable report' }));
    await waitFor(() => expect(useAppSettingsStore.getState().terminalBehavior.autoReportByErrorType.provider_rate_limited).toBe(false));
    expect(updateUiSettings).toHaveBeenCalledWith({ terminalBehavior: { autoReportByErrorType: { provider_rate_limited: false } } });

    cleanup();
    render(<TerminalBehaviorSettingsGroup />);
    expect(await screen.findByText('provider_rate_limited')).toBeTruthy();
  });

  it('selects a suggestion with the keyboard without saving immediately', async () => {
    render(<TerminalBehaviorSettingsGroup />);
    const input = screen.getByRole('combobox', { name: 'Runtime error classification' });
    fireEvent.change(input, { target: { value: 'rate' } });
    expect(await screen.findByRole('option', { name: 'cbc_rate_limited' })).toBeTruthy();
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect((input as HTMLInputElement).value).toBe('provider_rate_limited');
    expect(updateUiSettings).not.toHaveBeenCalled();
  });

  it('keeps manual classification entry working when the schema cannot be loaded', async () => {
    vi.mocked(fetchRuntimeIssueSchema).mockRejectedValue(new Error('schema offline'));
    render(<TerminalBehaviorSettingsGroup />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Runtime error classification' }), { target: { value: 'custom_provider_error' } });
    fireEvent.click(screen.getByRole('button', { name: 'Disable report' }));
    await waitFor(() => expect(useAppSettingsStore.getState().terminalBehavior.autoReportByErrorType.custom_provider_error).toBe(false));
    expect(updateUiSettings).toHaveBeenCalledWith({ terminalBehavior: { autoReportByErrorType: { custom_provider_error: false } } });
  });
});
