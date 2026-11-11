// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import JobsView from './JobsView';
import { useJobStore } from '@/stores/jobStore';
import type { Job, JobKind } from '@/types/jobs';

const api = vi.hoisted(() => ({
  updateActionJob: vi.fn(), canonicalRequest: vi.fn(),
  fetchActionRegistry: vi.fn(), fetchCompleteJobTemplates: vi.fn(),
  fetchJobs: vi.fn(),
  fetchJob: vi.fn(),
  fetchJobKinds: vi.fn(),
  fetchJobRuns: vi.fn(),
  fetchCompletedJobRetentionSettings: vi.fn(),
  updateCompletedJobRetentionSettings: vi.fn(),
  pinJob: vi.fn(),
  patchJob: vi.fn(),
  deleteJob: vi.fn(),
  cancelJob: vi.fn(),
}));
const wsHandlers = vi.hoisted(() => new Map<string, (event: unknown) => void>());

vi.mock('@/services/api', () => ({
  fetchLegacyJob: async () => ({ jobId: 'legacy' }),
  retryLegacyJob: vi.fn(),
  canonicalRequest: api.canonicalRequest,
  updateActionJob: api.updateActionJob,
  fetchActionRegistry: api.fetchActionRegistry,
  fetchCompleteJobTemplates: api.fetchCompleteJobTemplates,
  fetchJobs: api.fetchJobs,
  fetchJob: api.fetchJob,
  fetchJobKinds: api.fetchJobKinds,
  fetchJobRuns: api.fetchJobRuns,
  fetchCompletedJobRetentionSettings: api.fetchCompletedJobRetentionSettings,
  updateCompletedJobRetentionSettings: api.updateCompletedJobRetentionSettings,
  pinJob: api.pinJob,
  patchJob: api.patchJob,
  deleteJob: api.deleteJob,
  cancelJob: api.cancelJob,
  createJob: vi.fn(),
  runJobNow: vi.fn(),
}));
vi.mock('@/services/legacyJobs', () => ({
  fetchJobs: api.fetchJobs,
  fetchJob: api.fetchJob,
  fetchJobKinds: api.fetchJobKinds,
  fetchJobRuns: api.fetchJobRuns,
  patchJob: api.patchJob,
  cancelJob: api.cancelJob, deleteJob: api.deleteJob, pinJob: api.pinJob, runJobNow: vi.fn(),
}));
vi.mock('@/services/ws', () => ({
  wsClient: {
    on: (name: string, callback: (event: unknown) => void) => {
      wsHandlers.set(name, callback);
      return () => wsHandlers.delete(name);
    },
  },
}));
vi.mock('@/stores/uiStore', () => ({
  useUIStore: (selector: (state: { showToast: () => void }) => unknown) =>
    selector({ showToast: vi.fn() }),
}));
vi.mock('@/stores/sessionStore', () => ({
  useSessionStore: (
    selector: (state: { sessions: never[]; loadSessions: () => Promise<void> }) => unknown,
  ) => selector({ sessions: [], loadSessions: async () => {} }),
}));

const job = (overrides: Partial<Job> = {}): Job => ({
  jobId: 'job-1',
  kind: 'session-message',
  status: 'pending',
  name: 'Reminder',
  description: '',
  source: { type: 'agent' },
  target: { sessionId: 'session-1' },
  paused: false,
  schedule: { type: 'legacy-weekly' },
  runCount: 0,
  createdAt: 1,
  updatedAt: 1,
  ...overrides,
});

const scheduledJob = (overrides: Partial<Job> = {}): Job =>
  job({
    kind: 'scheduled-task',
    status: 'scheduled',
    schedule: [
      {
        id: 'entry-1',
        kind: 'interval',
        intervalSec: 3600,
        enabled: true,
        nextFireAt: '2026-09-27T09:00:00',
      },
    ],
    ...overrides,
  });

function renderView() {
  const view = render(
    <MemoryRouter>
      <JobsView />
    </MemoryRouter>,
  );
  return view;
}

function enterSelectionMode() {
  fireEvent.click(screen.getByRole('button', { name: 'Select jobs' }));
}

function selectAllVisible() {
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select all visible jobs' }));
}

const defaultRetentionRules = (): Record<MockRetentionRule, { enabled: boolean; days: number | null }> => ({
  completed: { enabled: false, days: null },
  failed: { enabled: false, days: null },
  timed_out: { enabled: false, days: null },
  cancelled: { enabled: false, days: null },
  logs: { enabled: false, days: null },
});

type MockRetentionRule = 'completed' | 'failed' | 'timed_out' | 'cancelled' | 'logs';
type MockRetentionRun = { at: number; expired: number; protected: number; errors: Array<{runId: string; attemptId: string; code: string}> } | null;

const defaultRetentionRuns = (): Record<MockRetentionRule, MockRetentionRun> => ({
  completed: null,
  failed: null,
  timed_out: null,
  cancelled: null,
  logs: null,
});

function retentionResponse(
  rules = defaultRetentionRules(),
  lastRuns: Record<MockRetentionRule, MockRetentionRun> = defaultRetentionRuns(),
) {
  return {
    revision: 0,
    cleanupScope: 'run_history',
    requestScope: 'scope-a',
    rules,
    configValidity: {
      completed: true,
      failed: true,
      timed_out: true,
      cancelled: true,
      logs: true,
    },
    lastRuns,
  };
}

describe('JobsView search, status filters, and bulk actions', () => {
  it('keeps legacy records visible as read-only rows in the single Jobs list', async () => {
    const old = job({ name: 'Old process', kind: 'background-process', status: 'failed' });
    const fresh = scheduledJob({ jobId: 'job_new', name: 'New Action Job', unified: {
      action: { api: 'action_reference', actionId: 'a', arguments: {} },
      config: { name: 'New Action Job', actionId: 'a', arguments: {}, schedule: [], enabled: true },
    } });
    const projection = { ...old, jobId: 'job_projection', name: 'Compatibility only', unified: {
      ...fresh.unified!, compatibility: { legacyKind: 'background-process', migration: 'projection-only' as const },
    } };
    api.fetchJobs.mockResolvedValue([old, projection, fresh]);
    api.fetchJob.mockResolvedValue(projection);
    renderView();
    expect(await screen.findByText('Old process')).toBeTruthy();
    expect(screen.getByText('Compatibility only')).toBeTruthy();
    expect(screen.getByText('New Action Job')).toBeTruthy();
    fireEvent.click(screen.getByText('Compatibility only'));
    expect(await screen.findByLabelText('Legacy execution details')).toBeTruthy();
    expect(screen.queryByText('Unified execution')).toBeNull();
    expect(screen.queryByText('Action reference')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Close$/ }));
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Compatibility' } });
    expect(screen.queryByText('New Action Job')).toBeNull();
    expect((screen.getByRole('searchbox') as HTMLInputElement).value).toBe('Compatibility');
  });
  it('shows canonical process run counts without a legacy label and preserves explicit retirement badges', async () => {
    const canonical = job({ jobId: 'canonical-process', name: 'Canonical process', kind: 'background-process', status: 'scheduled', runCount: 3 });
    const retired = job({ jobId: 'old-process', name: 'Retired process', kind: 'background-process', status: 'failed', retirementStatus: 'Retired' });
    const invalid = job({ jobId: 'invalid-message', name: 'Invalid message', kind: 'session-message', status: 'failed', retirementStatus: 'Invalid' });
    api.fetchJobs.mockResolvedValue([canonical, retired, invalid]);
    renderView();
    await screen.findByText('Canonical process');
    const row = (name: string) => screen.getByText(name).parentElement!.parentElement!;
    expect(within(row('Canonical process')).getByText('runs 3')).toBeTruthy();
    expect(within(row('Canonical process')).queryByText(/legacy/i)).toBeNull();
    expect(within(row('Retired process')).getByText('Retired')).toBeTruthy();
    expect(within(row('Invalid message')).getByText('Invalid')).toBeTruthy();
  });
  it('keeps the opened config revision when WS replaces a dirty unified Job', async () => {
    const initial = scheduledJob({ name: 'Unified revision draft', enabled: true, configRevision: 7,
      unified: { action: { api: 'action_reference', actionId: 'a', arguments: {} }, config: {
        name: 'Unified revision draft', actionId: 'a', arguments: {}, schedule: [], trigger: { kind: 'immediate' }, enabled: true } } });
    api.fetchJobs.mockResolvedValue([initial]); api.updateActionJob.mockResolvedValue(initial);
    renderView();
    fireEvent.click(await screen.findByText('Unified revision draft'));
    const edit = await screen.findByRole('button', { name: /^Edit$/ });
    await waitFor(() => expect((edit as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(edit);
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Kept user draft' } });
    act(() => useJobStore.getState().upsertJob({ ...initial, configRevision: 8 }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Job' }));
    await waitFor(() => expect(api.updateActionJob).toHaveBeenCalled());
    expect(api.updateActionJob.mock.calls[0]?.[2]).toBe(7);
    expect(api.updateActionJob.mock.calls[0]?.[1].name).toBe('Kept user draft');
  });
  beforeEach(() => {
    vi.resetAllMocks();
    sessionStorage.clear();
    api.canonicalRequest.mockResolvedValue({ id: 'job-1', name: 'Owned Job', state: 'enabled', stateRevision: 1, configRevision: 7, steps: [] });
    api.fetchActionRegistry.mockResolvedValue({ actions: [] });
    api.fetchCompleteJobTemplates.mockResolvedValue([]);
    wsHandlers.clear();
    api.fetchJobs.mockResolvedValue([]);
    api.fetchJob.mockResolvedValue(undefined);
    api.fetchJobKinds.mockResolvedValue([
      { kind: 'session-message', label: '定时消息', hasSchedule: true, hasProcess: false },
      { kind: 'main-lifecycle', label: '服务生命周期', hasSchedule: false, hasProcess: false },
      { kind: 'scheduled-task', label: '定时任务', hasSchedule: true, hasProcess: false },
    ]);
    api.fetchJobRuns.mockResolvedValue([]);
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(retentionResponse());
    api.updateCompletedJobRetentionSettings.mockImplementation(async (body) =>
      ({ status: 'completed', requestKey: body.requestKey, result: { settings: { ...retentionResponse(body.rules), revision: 1 } } }),
    );
  });

  afterEach(() => {
    cleanup();
    wsHandlers.clear();
    // The job list now lives in the shared store; isolate it per test.
    useJobStore.setState({
      jobs: [], loaded: false, loading: false, error: null, pinMetadataUnavailable: false,
    });
  });

  it('keeps a pin through lifecycle events and restores ordinary ordering after unpin', async () => {
    const pinned = job({ jobId: 'pinned', name: 'Pinned reminder', status: 'completed', pinned: true });
    api.fetchJobs.mockResolvedValue([pinned, job({ jobId: 'running', name: 'Running process', status: 'running' })]);
    api.pinJob.mockResolvedValue({ ...pinned, pinned: false });
    const { container } = renderView();
    await screen.findByText('Pinned reminder');
    const names = () => Array.from(container.querySelectorAll('div[title]')).map((el) => el.getAttribute('title')).filter((v) => v === 'Pinned reminder' || v === 'Running process');
    expect(names()).toEqual(['Pinned reminder', 'Running process']);
    const { pinned: ignored, ...eventJob } = pinned;
    expect(ignored).toBe(true);
    act(() => useJobStore.getState().upsertJob({ ...eventJob, updatedAt: 99 }));
    expect(screen.getByLabelText('Pinned')).toBeTruthy();
    expect(names()).toEqual(['Pinned reminder', 'Running process']);
    fireEvent.click(screen.getAllByRole('button', { name: 'Job actions' })[0]!);
    fireEvent.click(screen.getByRole('menuitem', { name: /^Unpin$/ }));
    await waitFor(() => expect(screen.queryByLabelText('Pinned')).toBeNull());
    expect(api.pinJob).toHaveBeenCalledWith('pinned', false);
    expect(names()).toEqual(['Running process', 'Pinned reminder']);
  });

  it('shows unavailable pin metadata, disables pin, and leaves ordinary filters usable', async () => {
    api.fetchJobs.mockResolvedValue([job({ pinned: null, pinMetadataStatus: 'unavailable' })]);
    renderView();
    await screen.findByText('Reminder');
    expect(screen.getByRole('status').textContent).toContain('Pin states are unknown');
    fireEvent.click(screen.getByRole('button', { name: 'Job actions' }));
    const pin = screen.getByRole('menuitem', { name: 'Pin unavailable' });
    expect((pin as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(pin);
    expect(api.pinJob).not.toHaveBeenCalled();
    if (screen.queryByRole('region', { name: 'Job detail' })) fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search jobs' }), { target: { value: 'Reminder' } });
    expect(screen.getByTitle('Reminder')).toBeTruthy();
  });

  it('shows metadata failure for an empty list via the list envelope', async () => {
    api.fetchJobs.mockImplementation(async (onStatus) => { onStatus('unavailable'); return []; });
    renderView();
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Pin states are unknown'));
  });

  it('clears a cached pin when an update explicitly reports unknown metadata', async () => {
    const pinned = job({ pinned: true, pinMetadataStatus: 'available' });
    api.fetchJobs.mockResolvedValue([pinned, job({ jobId: 'cached', name: 'Cached pin', pinned: true })]);
    renderView();
    await screen.findByText('Cached pin');
    expect(screen.getAllByLabelText('Pinned')).toHaveLength(2);
    await screen.findAllByLabelText('Pinned');
    act(() => useJobStore.getState().upsertJob({ ...pinned, pinned: null, pinMetadataStatus: 'unavailable' }));
    expect(screen.queryByLabelText('Pinned')).toBeNull();
    expect(screen.getByRole('status').textContent).toContain('Pin states are unknown');
  });

  it('matches Completed and Timeout only to their persisted API statuses', async () => {
    const records = [
      job({
        jobId: 'completed',
        name: 'Completed lifecycle',
        status: 'completed',
        kind: 'main-lifecycle',
      }),
      job({
        jobId: 'timeout',
        name: 'Timed out lifecycle',
        status: 'timed_out',
        kind: 'main-lifecycle',
      }),
      job({
        jobId: 'ordinary-failure',
        name: 'Ordinary failure',
        status: 'failed',
        kind: 'main-lifecycle',
      }),
    ];
    api.fetchJobs.mockResolvedValue(records);
    renderView();

    expect(await screen.findByText('Completed lifecycle')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Completed' }));
    expect(screen.getByText('Completed lifecycle')).toBeTruthy();
    expect(screen.queryByText('Timed out lifecycle')).toBeNull();
    expect(screen.queryByText('Ordinary failure')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Timeout' }));
    expect(screen.getByText('Timed out lifecycle')).toBeTruthy();
    expect(screen.getByText('timed_out').className).toContain('text-danger');
    expect(screen.queryByText('Ordinary failure')).toBeNull();
    expect(screen.queryByText('Completed lifecycle')).toBeNull();
  });

  it('loads retention settings from the server and saves the edited values', async () => {
    const loadedRules = defaultRetentionRules();
    loadedRules.completed = { enabled: true, days: 12 };
    const loadedRuns = defaultRetentionRuns();
    loadedRuns.completed = {
        at: 1790470800,
        expired: 4,
        protected: 1,
        errors: [],
      };
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(
      retentionResponse(loadedRules, loadedRuns),
    );
    const savedRules = { ...loadedRules, completed: { enabled: true, days: 14 } };
    const savedRuns = defaultRetentionRuns();
    savedRuns.completed = {
        at: 1790470800,
        expired: 4,
        protected: 1,
        errors: [],
      };
    api.updateCompletedJobRetentionSettings.mockImplementation(async (body) =>
      ({ status: 'completed', requestKey: body.requestKey, result: { settings: { ...retentionResponse(savedRules, savedRuns), revision: 1 } } }),
    );
    renderView();
    expect(api.fetchCompletedJobRetentionSettings).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    const daysInput = await screen.findByRole('spinbutton', { name: 'Keep Completed Runs for days' });
    expect((daysInput as HTMLInputElement).value).toBe('12');
    expect((screen.getByRole('checkbox', { name: 'Enable Completed Runs cleanup' }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/expired 4/).textContent).toContain('expired 4');
    expect(api.fetchCompletedJobRetentionSettings).toHaveBeenCalledTimes(1);

    fireEvent.change(daysInput, { target: { value: '14' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(api.updateCompletedJobRetentionSettings).toHaveBeenCalledWith({ expectedRevision: 0, expectedRequestScope: 'scope-a', requestKey: expect.any(String), rules: savedRules }));
    expect((await screen.findByRole('status')).textContent).toContain('Settings saved');
  });

  it('loads blank retention days from server defaults', async () => {
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    for (const label of [
      'Completed Runs',
      'Failed Runs',
      'Timed out Runs',
      'Cancelled Runs',
      'Run log files',
    ]) {
      const input = await screen.findByRole('spinbutton', {
        name: `Keep ${label} for days`,
      });
      expect((input as HTMLInputElement).value).toBe('');
    }
  });

  it('loads and saves failed, timeout, cancelled, and log retention independently', async () => {
    const rules = defaultRetentionRules();
    rules.failed = { enabled: true, days: 11 };
    rules.timed_out = { enabled: false, days: 12 };
    rules.cancelled = { enabled: true, days: 13 };
    rules.logs = { enabled: false, days: 14 };
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(retentionResponse(rules));
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    for (const label of ['Failed Runs', 'Timed out Runs', 'Cancelled Runs', 'Run log files']) {
      expect(await screen.findByRole('spinbutton', { name: `Keep ${label} for days` })).toBeTruthy();
      expect(screen.getByRole('checkbox', { name: `Enable ${label} cleanup` })).toBeTruthy();
    }
    expect(screen.getByText(/Run may remain after its expired log is removed/)).toBeTruthy();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Enable Timed out Runs cleanup' }));
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Keep Run log files for days' }), {
      target: { value: '21' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    const expected = {
      ...rules,
      timed_out: { enabled: true, days: 12 },
      logs: { enabled: false, days: 21 },
    };
    await waitFor(() => expect(api.updateCompletedJobRetentionSettings).toHaveBeenCalledWith({ expectedRevision: 0, expectedRequestScope: 'scope-a', requestKey: expect.any(String), rules: expected }));
    expect((await screen.findByRole('status')).textContent).toContain('Settings saved');
  });

  it('keeps a cleared day count blank when saving an enabled rule', async () => {
    const rules = defaultRetentionRules();
    rules.completed = { enabled: true, days: 14 };
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(retentionResponse(rules));
    api.updateCompletedJobRetentionSettings.mockImplementation(async (body) =>
      ({ status: 'completed', requestKey: body.requestKey, result: { settings: { ...retentionResponse(body.rules), revision: 1 } } }),
    );
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    const daysInput = await screen.findByRole('spinbutton', {
      name: 'Keep Completed Runs for days',
    });
    fireEvent.change(daysInput, { target: { value: '' } });
    expect((daysInput as HTMLInputElement).value).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));

    await waitFor(() => expect(api.updateCompletedJobRetentionSettings).toHaveBeenCalledWith({ expectedRevision: 0, expectedRequestScope: 'scope-a', requestKey: expect.any(String), rules: {
      ...rules,
      completed: { enabled: true, days: null },
    } }));
    expect((daysInput as HTMLInputElement).value).toBe('');
    expect((await screen.findByRole('status')).textContent).toContain('Settings saved');
  });

  it('recovers an unknown save using the original request key without resending', async () => {
    api.updateCompletedJobRetentionSettings.mockRejectedValueOnce(new Error('connection lost'));
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const input = await screen.findByRole('spinbutton', { name: 'Keep Completed Runs for days' });
    fireEvent.change(input, { target: { value: '10' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect((await screen.findByRole('alert')).textContent).toContain('connection lost');
    const sent = api.updateCompletedJobRetentionSettings.mock.calls[0]![0];
    expect(sent).toMatchObject({ expectedRevision: 0, rules: { completed: { enabled: false, days: 10 } } });
    expect(sessionStorage.getItem('pan:job-retention-pending')).toContain(sent.requestKey);
    expect((screen.getByRole('button', { name: 'Save settings' }) as HTMLButtonElement).disabled).toBe(true);
    api.canonicalRequest.mockResolvedValueOnce({ status: 'completed', requestKey: sent.requestKey, result: { settings: { ...retentionResponse(sent.rules), revision: 1 } } });
    fireEvent.click(screen.getByRole('button', { name: 'Query original operation' }));
    await waitFor(() => expect(api.canonicalRequest).toHaveBeenCalledWith(`operations?operation=update&requestKey=${encodeURIComponent(sent.requestKey)}`));
    expect(await screen.findByText('Settings saved.')).toBeTruthy();
    expect(api.updateCompletedJobRetentionSettings).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem('pan:job-retention-pending')).toBeNull();
    expect((input as HTMLInputElement).value).toBe('10');
  });

  it('keeps an unknown save unresolved when the original lookup returns 404', async () => {
    api.updateCompletedJobRetentionSettings.mockRejectedValueOnce(new Error('response lost'));
    renderView(); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await screen.findByRole('spinbutton', { name: 'Keep Completed Runs for days' });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await screen.findByRole('button', { name: 'Query original operation' });
    api.canonicalRequest.mockRejectedValueOnce(Object.assign(new Error('not_found'), { status: 404 }));
    fireEvent.click(screen.getByRole('button', { name: 'Query original operation' }));
    expect((await screen.findByRole('alert')).textContent).toContain('remains unresolved');
    expect(sessionStorage.getItem('pan:job-retention-pending')).not.toBeNull();
    expect(api.updateCompletedJobRetentionSettings).toHaveBeenCalledTimes(1);
  });

  it('restores the original pending rules after reopening without resending', async () => {
    const original = { expectedRevision: 0, requestKey: 'original-key', rules: { ...retentionResponse().rules, completed: { enabled: true, days: 15 } } };
    sessionStorage.setItem('pan:job-retention-pending', JSON.stringify(original));
    renderView(); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const input = await screen.findByRole('spinbutton', { name: 'Keep Completed Runs for days' });
    await waitFor(() => expect((input as HTMLInputElement).value).toBe('15'));
    expect((screen.getByRole('checkbox', { name: 'Enable Completed Runs cleanup' }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('button', { name: 'Save settings' }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.updateCompletedJobRetentionSettings).not.toHaveBeenCalled();
    expect(JSON.parse(sessionStorage.getItem('pan:job-retention-pending')!)).toEqual(original);
  });

  it.each(['other-principal', 'other-profile', undefined])('preserves pending and blocks lookup for a different or missing scope: %s', async (scope) => {
    const original = { expectedRevision: 0, requestKey: 'original-key', rules: defaultRetentionRules(), ...(scope ? { requestScope: scope } : {}) };
    sessionStorage.setItem('pan:job-retention-pending', JSON.stringify(original));
    renderView(); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const button = await screen.findByRole('button', { name: 'Query original operation' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Save settings' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/cannot be verified; lookup is unavailable/)).toBeTruthy();
    expect(JSON.parse(sessionStorage.getItem('pan:job-retention-pending')!)).toEqual(original);
    expect(api.canonicalRequest).not.toHaveBeenCalled();
    expect(api.updateCompletedJobRetentionSettings).not.toHaveBeenCalled();
  });

  it('does not query when the authenticated scope changes after the page opens', async () => {
    const original = { expectedRevision: 0, requestKey: 'original-key', rules: defaultRetentionRules(), requestScope: 'scope-a' };
    sessionStorage.setItem('pan:job-retention-pending', JSON.stringify(original));
    renderView(); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const button = await screen.findByRole('button', { name: 'Query original operation' });
    api.fetchCompletedJobRetentionSettings.mockResolvedValueOnce({ ...retentionResponse(), requestScope: 'other-profile' });
    fireEvent.click(button);
    expect((await screen.findByRole('alert')).textContent).toContain('Account or profile changed');
    expect(api.canonicalRequest).not.toHaveBeenCalled();
    expect(JSON.parse(sessionStorage.getItem('pan:job-retention-pending')!)).toEqual(original);
  });

  it('keeps the original request unresolved if a receipt has a different request key', async () => {
    api.updateCompletedJobRetentionSettings.mockImplementationOnce(async (body) => ({ status: 'completed', requestKey: 'wrong-key', result: { settings: retentionResponse(body.rules) } }));
    renderView(); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await screen.findByRole('spinbutton', { name: 'Keep Completed Runs for days' });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect((await screen.findByRole('alert')).textContent).toContain('request key does not match');
    expect(JSON.parse(sessionStorage.getItem('pan:job-retention-pending')!).requestKey).not.toBe('wrong-key');
    expect((screen.getByRole('button', { name: 'Save settings' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('rejects a lookup receipt with a different key and preserves the original', async () => {
    const original = { expectedRevision: 0, requestKey: 'original-key', rules: defaultRetentionRules(), requestScope: 'scope-a' };
    sessionStorage.setItem('pan:job-retention-pending', JSON.stringify(original));
    api.canonicalRequest.mockResolvedValueOnce({ status: 'completed', requestKey: 'wrong-key', result: { settings: retentionResponse() } });
    renderView(); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Query original operation' }));
    expect((await screen.findByRole('alert')).textContent).toContain('request key does not match');
    expect(JSON.parse(sessionStorage.getItem('pan:job-retention-pending')!)).toEqual(original);
    expect(api.updateCompletedJobRetentionSettings).not.toHaveBeenCalled();
  });

  it('preserves the request if the scope changes while the lookup is in flight', async () => {
    const original = { expectedRevision: 0, requestKey: 'original-key', rules: defaultRetentionRules(), requestScope: 'scope-a' };
    sessionStorage.setItem('pan:job-retention-pending', JSON.stringify(original));
    api.canonicalRequest.mockResolvedValueOnce({ status: 'completed', requestKey: original.requestKey, result: { settings: retentionResponse() } });
    renderView(); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const button = await screen.findByRole('button', { name: 'Query original operation' });
    api.fetchCompletedJobRetentionSettings.mockResolvedValueOnce(retentionResponse()).mockResolvedValueOnce({ ...retentionResponse(), requestScope: 'other-principal' });
    fireEvent.click(button);
    expect((await screen.findByRole('alert')).textContent).toContain('Account or profile changed');
    expect(JSON.parse(sessionStorage.getItem('pan:job-retention-pending')!)).toEqual(original);
    expect(api.updateCompletedJobRetentionSettings).not.toHaveBeenCalled();
  });

  it('does not submit when the account or profile changes before saving', async () => {
    renderView(); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const input = await screen.findByRole('spinbutton', { name: 'Keep Completed Runs for days' });
    fireEvent.change(input, { target: { value: '21' } });
    api.fetchCompletedJobRetentionSettings.mockResolvedValueOnce({ ...retentionResponse(), requestScope: 'other-profile' });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Account or profile changed');
    expect((input as HTMLInputElement).value).toBe('21');
    expect(api.updateCompletedJobRetentionSettings).not.toHaveBeenCalled();
    expect(sessionStorage.getItem('pan:job-retention-pending')).toBeNull();
  });

  it('keeps the draft on a definite scope conflict without treating it as a saved write', async () => {
    api.updateCompletedJobRetentionSettings.mockRejectedValueOnce(Object.assign(new Error('request_scope_conflict'), { status: 409 }));
    renderView(); fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const input = await screen.findByRole('spinbutton', { name: 'Keep Completed Runs for days' });
    fireEvent.change(input, { target: { value: '21' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect((await screen.findByRole('alert')).textContent).toContain('request_scope_conflict');
    expect((input as HTMLInputElement).value).toBe('21');
    expect(sessionStorage.getItem('pan:job-retention-pending')).toBeNull();
    expect(screen.queryByText('Settings saved.')).toBeNull();
    expect(api.canonicalRequest).not.toHaveBeenCalled();
  });

  it('shows invalid persisted rule feedback and keeps that switch off', async () => {
    const response = retentionResponse();
    response.configValidity.failed = false;
    api.fetchCompletedJobRetentionSettings.mockResolvedValue(response);
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));

    expect((await screen.findByRole('alert')).textContent).toContain('Failed Runs');
    expect((screen.getByRole('checkbox', {
      name: 'Enable Failed Runs cleanup',
    }) as HTMLInputElement).checked).toBe(false);
  });

  it('shows settings load and save errors', async () => {
    api.fetchCompletedJobRetentionSettings.mockRejectedValueOnce(new Error('settings unavailable'));
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect((await screen.findByRole('alert')).textContent).toContain('settings unavailable');

    api.fetchCompletedJobRetentionSettings.mockResolvedValueOnce(retentionResponse());
    api.updateCompletedJobRetentionSettings.mockRejectedValueOnce(new Error('save failed'));
    cleanup();
    renderView();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await screen.findByRole('spinbutton', { name: 'Keep Completed Runs for days' });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect((await screen.findByRole('alert')).textContent).toContain('save failed');
  });

  it('combines case-insensitive name, ID, and description search with status and kind filters', async () => {
    api.fetchJobs.mockResolvedValue([
      job({
        jobId: 'job_timeout_alpha',
        name: 'Alpha lifecycle',
        status: 'timed_out',
        kind: 'main-lifecycle',
      }),
      job({
        jobId: 'job_timeout_beta',
        name: 'Beta lifecycle',
        status: 'timed_out',
        kind: 'main-lifecycle',
        description: 'Needle detail',
      }),
      job({
        jobId: 'job_timeout_task',
        name: 'Alpha task',
        status: 'timed_out',
        kind: 'scheduled-task',
      }),
      job({
        jobId: 'job_failed_alpha',
        name: 'Alpha failed',
        status: 'failed',
        kind: 'main-lifecycle',
      }),
    ]);
    renderView();
    expect(await screen.findByText('Alpha lifecycle')).toBeTruthy();

    enterSelectionMode();
    selectAllVisible();
    expect(screen.getByText('4 selected')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Timeout' }));
    expect(screen.getByText('0 selected')).toBeTruthy();
    fireEvent.change(screen.getByRole('combobox', { name: 'Kind' }), {
      target: { value: 'main-lifecycle' },
    });
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search jobs' }), {
      target: { value: 'BETA' },
    });
    expect(screen.getByText('Beta lifecycle')).toBeTruthy();
    expect(screen.queryByText('Alpha lifecycle')).toBeNull();
    expect(screen.queryByText('Alpha task')).toBeNull();
    expect(screen.queryByText('Alpha failed')).toBeNull();

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search jobs' }), {
      target: { value: 'needle' },
    });
    expect(screen.getByText('Beta lifecycle')).toBeTruthy();
    selectAllVisible();
    expect(screen.getByText('1 selected')).toBeTruthy();
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search jobs' }), {
      target: { value: 'missing-query' },
    });
    expect(screen.getByText('0 selected')).toBeTruthy();
    expect(screen.queryByText('Beta lifecycle')).toBeNull();
    expect(screen.getByText('No jobs match')).toBeTruthy();
  });

  it('keeps controls and counts outside the dedicated row scroll container', async () => {
    api.fetchJobs.mockResolvedValue([job()]);
    renderView();
    expect(await screen.findByText('Reminder')).toBeTruthy();
    const scroll = screen.getByTestId('jobs-list-scroll');
    expect(scroll.className).toContain('overflow-y-auto');
    expect(scroll.contains(screen.getByRole('button', { name: 'Completed' }))).toBe(false);
    expect(scroll.contains(screen.getByRole('searchbox', { name: 'Search jobs' }))).toBe(false);
    expect(scroll.contains(screen.getByText('1 of 1 jobs'))).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'New Job' }));
    expect(screen.queryByTestId('jobs-list-scroll')).toBeNull();
    expect(screen.getByRole('button', { name: 'New Job' }).getAttribute('aria-pressed')).toBe(
      'true',
    );
    expect(screen.getByRole('textbox', { name: 'Name' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Create job' })).toBeTruthy();
  });

  it('selects and deselects visible results without opening a detail drawer; row menus stay isolated', async () => {
    api.fetchJobs.mockResolvedValue([
      job({ jobId: 'job-a', name: 'Job A' }),
      job({ jobId: 'job-b', name: 'Job B' }),
    ]);
    renderView();
    expect(await screen.findByText('Job A')).toBeTruthy();
    enterSelectionMode();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select job Job A' }));
    expect(screen.queryByRole('region', { name: 'Job detail' })).toBeNull();
    expect(screen.getByText('1 selected')).toBeTruthy();
    selectAllVisible();
    expect(screen.getByText('2 selected')).toBeTruthy();
    selectAllVisible();
    expect(screen.getByText('0 selected')).toBeTruthy();

    const row = screen.getByText('Job A').closest('.rounded.border') as HTMLElement;
    fireEvent.click(within(row).getByRole('button', { name: 'Job actions' }));
    expect(await screen.findByRole('menu')).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Job detail' })).toBeNull();
  });

  it('pauses unpaused jobs with paused=true, reports partial failure, and retries only failures', async () => {
    const jobs = [
      scheduledJob({ jobId: 'pause-ok', name: 'Pause OK', paused: false }),
      scheduledJob({ jobId: 'pause-fail', name: 'Pause Fail', paused: false }),
      scheduledJob({ jobId: 'already-paused', name: 'Already Paused', paused: true }),
    ];
    api.fetchJobs.mockResolvedValue(jobs);
    api.patchJob.mockImplementation(async (id: string) => {
      if (
        id === 'pause-fail' &&
        api.patchJob.mock.calls.filter(([called]) => called === id).length === 1
      ) {
        throw new Error('temporary pause failure');
      }
      return { ...jobs.find((item) => item.jobId === id)!, paused: true };
    });
    renderView();
    expect(await screen.findByText('Pause OK')).toBeTruthy();
    enterSelectionMode();
    selectAllVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Pause scheduled tasks' }));

    expect(await screen.findByText(/Pause: 2 succeeded, 1 failed/)).toBeTruthy();
    expect(api.patchJob).toHaveBeenCalledTimes(2);
    expect(api.patchJob).toHaveBeenCalledWith('pause-ok', { paused: true });
    expect(api.patchJob).toHaveBeenCalledWith('pause-fail', { paused: true });
    expect(screen.getByText('1 selected')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Retry failed (1)' }));
    await waitFor(() => expect(api.patchJob).toHaveBeenCalledTimes(3));
    expect(api.patchJob.mock.calls[2]).toEqual(['pause-fail', { paused: true }]);
    expect(await screen.findByText(/Pause: 1 succeeded, 0 failed/)).toBeTruthy();
    expect(screen.getByText('0 selected')).toBeTruthy();
  });

  it('blocks duplicate bulk submission while per-job pause requests are pending', async () => {
    const pendingJob = scheduledJob({ jobId: 'pending-pause', name: 'Pending pause' });
    let resolvePatch: ((value: Job) => void) | undefined;
    api.fetchJobs.mockResolvedValue([pendingJob]);
    api.patchJob.mockImplementation(
      () =>
        new Promise<Job>((resolve) => {
          resolvePatch = resolve;
        }),
    );
    renderView();
    expect(await screen.findByText('Pending pause')).toBeTruthy();
    enterSelectionMode();
    selectAllVisible();
    const pauseButton = screen.getByRole('button', { name: 'Pause scheduled tasks' });
    fireEvent.click(pauseButton);
    fireEvent.click(pauseButton);

    expect(await screen.findByText('Pausing selected jobs…')).toBeTruthy();
    expect(api.patchJob).toHaveBeenCalledTimes(1);
    resolvePatch?.({ ...pendingJob, paused: true });
    expect(await screen.findByText(/Pause: 1 succeeded, 0 failed/)).toBeTruthy();
  });

  it('cancels batch delete without requests and confirms the displayed quantity', async () => {
    api.fetchJobs.mockResolvedValue([
      job({ jobId: 'delete-a', name: 'Delete A' }),
      job({ jobId: 'delete-b', name: 'Delete B' }),
    ]);
    renderView();
    expect(await screen.findByText('Delete A')).toBeTruthy();
    enterSelectionMode();
    selectAllVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Delete selected (2)' }));
    const dialog = await screen.findByRole('dialog', { name: 'Delete selected jobs' });
    expect(dialog.textContent).toContain('Delete 2 selected jobs?');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog', { name: 'Delete selected jobs' })).toBeNull();
    expect(api.deleteJob).not.toHaveBeenCalled();
    expect(screen.getByText('2 selected')).toBeTruthy();
  });

  it('removes WS-deleted selection before confirmation and retries only a failed DELETE', async () => {
    const jobs = [
      job({ jobId: 'delete-ws', name: 'Deleted elsewhere' }),
      job({ jobId: 'delete-ok', name: 'Delete OK' }),
      job({ jobId: 'delete-retry', name: 'Delete Retry' }),
    ];
    api.fetchJobs.mockResolvedValue(jobs);
    api.deleteJob.mockImplementation(async (id: string) => {
      if (
        id === 'delete-retry' &&
        api.deleteJob.mock.calls.filter(([called]) => called === id).length === 1
      ) {
        throw new Error('temporary delete failure');
      }
      return { deleted: true };
    });
    renderView();
    expect(await screen.findByText('Deleted elsewhere')).toBeTruthy();
    enterSelectionMode();
    selectAllVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Delete selected (3)' }));

    // A deletion elsewhere arrives through the shared store (global WS wiring).
    act(() => useJobStore.getState().removeJob('delete-ws'));
    const dialog = await screen.findByRole('dialog', { name: 'Delete selected jobs' });
    expect(dialog.textContent).toContain('Delete 2 selected jobs?');

    fireEvent.click(screen.getByRole('button', { name: 'Delete 2 jobs' }));
    expect(await screen.findByText(/Delete: 1 succeeded, 1 failed/)).toBeTruthy();
    expect(api.deleteJob).toHaveBeenCalledTimes(2);
    expect(api.deleteJob).toHaveBeenCalledWith('delete-ok');
    expect(api.deleteJob).toHaveBeenCalledWith('delete-retry');
    expect(screen.getByText('Delete Retry: temporary delete failure')).toBeTruthy();
    expect(screen.getByText('1 selected')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Retry failed (1)' }));
    await waitFor(() => expect(api.deleteJob).toHaveBeenCalledTimes(3));
    expect(api.deleteJob.mock.calls[2]).toEqual(['delete-retry']);
    expect(await screen.findByText(/Delete: 1 succeeded, 0 failed/)).toBeTruthy();
    expect(screen.getByText('0 selected')).toBeTruthy();
  });

  it('supports searching by kind payload ID and description while retaining kind filters', async () => {
    const payloadKind: JobKind = 'main-lifecycle';
    api.fetchJobs.mockResolvedValue([
      job({ jobId: 'id-search-hit', name: 'Lifecycle A', status: 'timed_out', kind: payloadKind }),
      job({
        jobId: 'no-hit',
        name: 'Lifecycle B',
        description: 'find me in description',
        status: 'timed_out',
        kind: payloadKind,
      }),
    ]);
    renderView();
    expect(await screen.findByText('Lifecycle A')).toBeTruthy();
    fireEvent.change(screen.getByRole('combobox', { name: 'Kind' }), {
      target: { value: payloadKind },
    });
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search jobs' }), {
      target: { value: 'ID-SEARCH' },
    });
    expect(screen.getByText('Lifecycle A')).toBeTruthy();
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search jobs' }), {
      target: { value: 'FIND ME' },
    });
    expect(screen.getByText('Lifecycle B')).toBeTruthy();
  });
});


describe('Job cancellation and ordering', () => {
  beforeEach(() => { vi.resetAllMocks(); localStorage.clear(); useJobStore.setState({ jobs: [], loading: false, error: null }); api.fetchJobKinds.mockResolvedValue([]); api.fetchJobs.mockResolvedValue([]); api.fetchJobRuns.mockResolvedValue([]); });
  afterEach(cleanup);
  it('retains a failed cancellation and prevents duplicate requests', async () => {
    api.fetchJobs.mockResolvedValue([job({ status: 'running', label: 'Build validation' })]);
    let reject!: (error: Error) => void;
    api.cancelJob.mockImplementation(() => new Promise((_, r) => { reject = r; }));
    renderView();
    const button = await screen.findByRole('button', { name: 'Cancel job' });
    fireEvent.click(button); fireEvent.click(button);
    expect(api.cancelJob).toHaveBeenCalledTimes(1);
    expect(screen.getAllByRole('button', { name: 'Cancelling…' }).every(e => (e as HTMLButtonElement).disabled)).toBe(true);
    await act(async () => reject(new Error('Cancellation refused')));
    expect((await screen.findAllByRole('button', { name: 'Cancel job' })).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Reminder').length).toBeGreaterThan(0);
    expect(api.deleteJob).not.toHaveBeenCalled();
  });
  it('sorts equal timestamps by ID and persists direction while live updates reorder', async () => {
    api.fetchJobs.mockResolvedValue([job({ jobId: 'b', name: 'Second', updatedAt: 2 }), job({ jobId: 'a', name: 'First', updatedAt: 2 })]);
    renderView(); await screen.findByText('First');
    fireEvent.change(screen.getByLabelText('Sort jobs'), { target: { value: 'updated' } });
    expect(screen.getAllByText(/First|Second/).map(e => e.textContent)).toEqual(['First', 'Second']);
    act(() => useJobStore.getState().upsertJob(job({ jobId: 'b', name: 'Second', updatedAt: 3 })));
    expect(screen.getAllByText(/First|Second/).map(e => e.textContent)).toEqual(['Second', 'First']);
    fireEvent.click(screen.getByRole('button', { name: 'Descending' }));
    expect(JSON.parse(localStorage.getItem('pan:jobsSort')!).direction).toBe('asc');
  });
});


it('sorts created time and actual kind safely with missing legacy times last', async () => {
  localStorage.clear();
  api.fetchJobKinds.mockResolvedValue([]);
  api.fetchJobs.mockResolvedValue([
    job({ jobId: 'b', name: 'Beta', kind: 'session-message', createdAt: '2026-01-02T00:00:00Z' }),
    job({ jobId: 'a', name: 'Alpha', kind: 'background-process', createdAt: 1 }),
    job({ jobId: 'c', name: 'Legacy', createdAt: 'invalid' }),
  ]);
  renderView(); await screen.findByText('Alpha');
  const names = () => screen.getAllByText(/^(Alpha|Beta|Legacy)$/).map(e => e.textContent);
  fireEvent.change(screen.getByLabelText('Sort jobs'), { target: { value: 'created' } });
  expect(names()).toEqual(['Beta', 'Alpha', 'Legacy']);
  fireEvent.click(screen.getByRole('button', { name: 'Descending' }));
  expect(names()).toEqual(['Alpha', 'Beta', 'Legacy']);
  fireEvent.change(screen.getByLabelText('Sort jobs'), { target: { value: 'kind' } });
  expect(names()).toEqual(['Alpha', 'Beta', 'Legacy']);
  cleanup();
});


it('keeps accepted asynchronous cancellation disabled until the live terminal event', async () => {
  localStorage.clear();
  const running = job({ jobId: 'async-cancel', status: 'running' });
  api.fetchJobs.mockResolvedValue([running]); api.fetchJobKinds.mockResolvedValue([]);
  api.cancelJob.mockResolvedValue(undefined); api.fetchJob.mockResolvedValue(running);
  renderView(); fireEvent.click(await screen.findByRole('button', { name: 'Cancel job' }));
  await waitFor(() => expect(api.fetchJob).toHaveBeenCalledWith('async-cancel'));
  expect((screen.getByRole('button', { name: 'Cancelling…' }) as HTMLButtonElement).disabled).toBe(true);
  act(() => useJobStore.getState().upsertJob({ ...running, status: 'cancelled', updatedAt: 2 }));
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Cancelling…' })).toBeNull());
  cleanup();
});
