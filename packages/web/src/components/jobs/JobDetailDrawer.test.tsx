// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { JobDetailDrawer } from './JobDetailDrawer';
import type { Job } from '@/types/jobs';

const job: Job = {
  jobId: 'job-session-message',
  kind: 'session-message',
  status: 'pending',
  name: 'Session message',
  description: '',
  source: { type: 'agent' },
  target: { sessionId: 'session-1' },
  paused: false,
  schedule: [],
  runCount: 0,
  createdAt: 1,
  updatedAt: 1,
};

describe('JobDetailDrawer width', () => {
  afterEach(() => cleanup());

  it('fills mobile width and caps the desktop panel at 28rem', () => {
    render(
      <JobDetailDrawer
        job={job}
        kindLabel="Session message"
        runs={[]}
        runsLoading={false}
        onLoadMoreRuns={vi.fn()}
        onClose={vi.fn()}
        onRunNow={vi.fn()}
        onTogglePaused={vi.fn()}
        onDelete={vi.fn()}
        onChangeTarget={vi.fn()}
        onEdit={vi.fn()}
        onToggleEntryEnabled={vi.fn()}
      />,
    );

    const drawer = screen.getByRole('dialog', { name: 'Job detail' });
    expect(drawer.className).toContain('w-full');
    expect(drawer.className).toContain('md:max-w-[28rem]');
    expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy();
  });

  it('shows dynamic targeting and permits run-now for a targetless resume action', () => {
    const resumeJob: Job = {
      ...job,
      jobId: 'job_resume',
      kind: 'scheduled-task',
      name: 'Resume legal running Sessions',
      source: { type: 'system' },
      target: { sessionId: null },
      action: { api: 'resume_legal_running' },
      text: '继续',
      status: 'scheduled',
      schedule: [],
    };
    render(
      <JobDetailDrawer
        job={resumeJob}
        kindLabel="Scheduled task"
        runs={[]}
        runsLoading={false}
        onLoadMoreRuns={vi.fn()}
        onClose={vi.fn()}
        onRunNow={vi.fn()}
        onTogglePaused={vi.fn()}
        onDelete={vi.fn()}
        onChangeTarget={vi.fn()}
        onEdit={vi.fn()}
        onToggleEntryEnabled={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Run now' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Change target' })).toBeNull();
    expect(screen.getAllByText(/dynamic: legal running Sessions without a live Worker/).length)
      .toBeGreaterThan(0);
    expect(screen.getByText('继续')).toBeTruthy();
  });
});
