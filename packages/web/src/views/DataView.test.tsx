// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import DataView from './DataView';
import { fetchCompletedJobRetentionSettings } from '@/services/api';
import type { JobRetentionRules } from '@/services/api';

vi.mock('@/services/api', () => ({
  fetchCompletedJobRetentionSettings: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const rules: JobRetentionRules = {
  completed: { enabled: true, days: null },
  failed: { enabled: false, days: null },
  timed_out: { enabled: true, days: 12 },
  cancelled: { enabled: false, days: null },
  logs: { enabled: true, days: 4 },
};

describe('DataView Jobs retention projection', () => {
  it('reads the shared Jobs settings API and projects null without a local default', async () => {
    vi.mocked(fetchCompletedJobRetentionSettings).mockResolvedValue({
      settings: rules.completed,
      rules,
      configValid: true,
      configValidity: {
        completed: true,
        failed: true,
        timed_out: true,
        cancelled: true,
        logs: true,
      },
      lastRun: null,
      lastRuns: {
        completed: null,
        failed: null,
        timed_out: null,
        cancelled: null,
        logs: null,
      },
    });

    render(<MemoryRouter><DataView /></MemoryRouter>);

    expect(await screen.findByText('Enabled, but no day count; this rule will not run')).toBeTruthy();
    expect(screen.getByText('Enabled · keep 12 days')).toBeTruthy();
    expect(screen.getAllByText('Disabled')).toHaveLength(2);
    expect(fetchCompletedJobRetentionSettings).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('link', { name: 'Open Jobs settings' }).getAttribute('href')).toBe('/jobs');
  });

  it('shows errors from the shared settings request', async () => {
    vi.mocked(fetchCompletedJobRetentionSettings).mockRejectedValue(new Error('API unavailable'));
    render(<MemoryRouter><DataView /></MemoryRouter>);

    expect((await screen.findByRole('alert')).textContent).toContain('API unavailable');
  });
});
