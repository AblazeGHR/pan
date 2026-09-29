// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { JobRetentionSettings } from './JobRetentionSettings';
import type { JobRetentionRule, JobRetentionRules } from '@/services/api';

const api = vi.hoisted(() => ({
  fetchCompletedJobRetentionSettings: vi.fn(),
  updateCompletedJobRetentionSettings: vi.fn(),
}));

vi.mock('@/services/api', () => ({
  fetchCompletedJobRetentionSettings: api.fetchCompletedJobRetentionSettings,
  updateCompletedJobRetentionSettings: api.updateCompletedJobRetentionSettings,
}));

const retentionRules = (): JobRetentionRules => ({
  completed: { enabled: false, days: null },
  failed: { enabled: false, days: null },
  timed_out: { enabled: false, days: null },
  cancelled: { enabled: false, days: null },
  logs: { enabled: false, days: null },
});

function retentionResponse(rules = retentionRules()) {
  const configValidity: Record<JobRetentionRule, boolean> = {
    completed: true,
    failed: true,
    timed_out: true,
    cancelled: true,
    logs: true,
  };
  const lastRuns = {
    completed: null,
    failed: null,
    timed_out: null,
    cancelled: null,
    logs: null,
  };
  return {
    settings: rules.completed,
    rules,
    configValid: true,
    configValidity,
    lastRun: null,
    lastRuns,
  };
}

describe('JobRetentionSettings shared editor', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(retentionResponse());
    api.updateCompletedJobRetentionSettings.mockImplementation(async (rules) => retentionResponse(rules));
  });

  afterEach(() => cleanup());

  it('loads all five rules from the shared API and keeps null days blank', async () => {
    render(<JobRetentionSettings />);

    for (const label of [
      'Completed Jobs',
      'Failed Jobs',
      'Timed out Jobs',
      'Cancelled Jobs',
      'Job log files',
    ]) {
      const daysInput = await screen.findByRole('spinbutton', { name: `Keep ${label} for days` });
      expect((daysInput as HTMLInputElement).value).toBe('');
      expect(daysInput.closest('label')?.className).toContain('max-w-[24rem]');
      expect((screen.getByRole('checkbox', { name: `Enable ${label} cleanup` }) as HTMLInputElement).checked).toBe(false);
    }
    expect(api.fetchCompletedJobRetentionSettings).toHaveBeenCalledTimes(1);
  });

  it('saves a blank day count as null even when that rule is enabled', async () => {
    const loaded = retentionRules();
    loaded.completed = { enabled: true, days: 14 };
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(retentionResponse(loaded));

    render(<JobRetentionSettings />);

    const daysInput = await screen.findByRole('spinbutton', { name: 'Keep Completed Jobs for days' });
    fireEvent.change(daysInput, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));

    await waitFor(() => expect(api.updateCompletedJobRetentionSettings).toHaveBeenCalledWith({
      ...loaded,
      completed: { enabled: true, days: null },
    }));
    expect((await screen.findByRole('status')).textContent).toContain('Settings saved');
    expect((daysInput as HTMLInputElement).value).toBe('');
  });

  it('validates entered days and reports API errors', async () => {
    render(<JobRetentionSettings />);

    const daysInput = await screen.findByRole('spinbutton', { name: 'Keep Failed Jobs for days' });
    fireEvent.change(daysInput, { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect((await screen.findByRole('alert')).textContent).toContain('whole number from 1 to 36500');
    expect(api.updateCompletedJobRetentionSettings).not.toHaveBeenCalled();

    api.fetchCompletedJobRetentionSettings.mockRejectedValueOnce(new Error('API unavailable'));
    cleanup();
    render(<JobRetentionSettings />);
    expect((await screen.findByRole('alert')).textContent).toContain('API unavailable');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });
});
