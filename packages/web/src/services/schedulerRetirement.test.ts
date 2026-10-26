import { expect, it, vi } from 'vitest';

it('exports no old task transport and makes no request during import', async () => {
  vi.resetModules();
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  try {
    const api = await import('./api');
    for (const name of [
      'fetchScheduledTasks', 'createScheduledTask', 'updateScheduledTask',
      'deleteScheduledTask', 'pauseScheduledTask', 'resumeScheduledTask',
      'runScheduledTaskNow', 'fetchTaskRuns', 'fetchSchedulerNext', 'fetchSchedulerStatus',
    ]) {
      expect(name in api).toBe(false);
    }
    expect(fetch).not.toHaveBeenCalled();
  } finally {
    vi.unstubAllGlobals();
  }
});
