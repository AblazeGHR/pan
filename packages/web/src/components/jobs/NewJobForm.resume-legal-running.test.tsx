// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { NewJobForm } from './NewJobForm';
import type { Job } from '@/types/jobs';

vi.mock('@/stores/sessionStore', () => ({
  useSessionStore: (selector: (state: { sessions: never[] }) => unknown) =>
    selector({ sessions: [] }),
}));

describe('NewJobForm resume legal running template', () => {
  afterEach(() => cleanup());

  it('creates a scheduled action with a dynamic target and fixed message', () => {
    const onCreate = vi.fn();
    render(<NewJobForm onCreate={onCreate} />);

    fireEvent.click(screen.getByRole('button', {
      name: '唤醒所有合法 running 的 Session',
    }));
    fireEvent.click(screen.getByRole('button', { name: 'Create job' }));

    expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'scheduled-task',
      target: { sessionId: null },
      action: { api: 'resume_legal_running' },
      text: '继续',
      schedule: expect.any(Array),
    }));
    expect(screen.getByText(/每次 Job 触发时/)).toBeTruthy();
  });

  it('preserves the action while editing its schedule and metadata', () => {
    const onSave = vi.fn();
    const job: Job = {
      jobId: 'job_resume',
      kind: 'scheduled-task',
      status: 'scheduled',
      name: 'Resume legal running Sessions',
      description: '',
      source: { type: 'system' },
      target: { sessionId: null },
      action: { api: 'resume_legal_running' },
      text: '继续',
      paused: false,
      schedule: [{
        id: 'entry_1', kind: 'interval', intervalSec: 3600,
        enabled: true, misfirePolicy: 'fire_now', nextFireAt: null,
      }],
      runCount: 0,
      createdAt: 1,
      updatedAt: 1,
    };
    render(<NewJobForm mode="edit" initialJob={job} onSave={onSave} />);

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({
      action: { api: 'resume_legal_running' },
      target: { sessionId: null },
      text: '继续',
      schedule: expect.any(Array),
    }));
  });
});
