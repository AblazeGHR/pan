// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
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

  it('shows structured original notification receipts and only offers target editing for pending notices', () => {
    const edit = vi.fn();
    const detail = { job: { id: 'job1', name: 'Action Job', state: 'enabled', revision: 1, stateRevision: 1,
      notification: { runtime: { owner: 'notification-manager' } } }, actions: {}, missing: [] };
    render(<JobDetailDrawer inline job={{ ...job, kind: 'scheduled-task' }} kindLabel="Action Job" isActionJob canonicalDetail={detail}
      definitionStatus="ready" runsLoading={false} runs={[{ run_id: 'run1', status: 'completed', notifications: [
        { status: 'pending', targetRef: 'session-a', outboxId: 'outbox-pending' },
        { status: 'unknown', targetRef: 'session-b', outboxId: 'outbox-unknown', runtimeOperationId: 'operation-original' },
        { status: 'acked', targetRef: 'session-c', outboxId: 'outbox-acked' },
      ] }]} onLoadMoreRuns={vi.fn()} onClose={vi.fn()} onRunNow={vi.fn()} onTogglePaused={vi.fn()}
      onDelete={vi.fn()} onChangeTarget={vi.fn()} onEdit={edit} onToggleEntryEnabled={vi.fn()} />);
    const receipts = within(screen.getByLabelText('Notification receipts run1'));
    expect(receipts.getByText('Status: pending')).toBeTruthy();
    expect(receipts.getByText('Target: session-b')).toBeTruthy();
    expect(receipts.getByText('operation-original')).toBeTruthy();
    expect(receipts.getByText(/outcome is unknown/)).toBeTruthy();
    expect(receipts.getAllByRole('button', { name: 'Edit notification target' })).toHaveLength(1);
    fireEvent.click(receipts.getByRole('button', { name: 'Edit notification target' }));
    expect(edit).toHaveBeenCalledTimes(1);
  });

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
      name: 'Recover Illegal Sessions',
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
    expect(screen.getAllByText(/dynamic: Illegal Sessions pending recovery without a live Worker/).length)
      .toBeGreaterThan(0);
    expect(screen.getByText('Configured in Settings → Behavior → Wake message')).toBeTruthy();
  });

  it('shows the QQ contact and bot identity and permits run-now without a Session target', () => {
    const qqJob: Job = {
      ...job,
      jobId: 'job_qq',
      kind: 'scheduled-task',
      name: 'Scheduled QQ message',
      target: { sessionId: null },
      action: { api: 'send_qq', args: {
        targetType: 'group', targetId: '654321', botUin: '123456',
      } },
      text: 'Scheduled text',
      status: 'scheduled',
      schedule: [],
    };
    render(
      <JobDetailDrawer
        job={qqJob}
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
    expect(screen.getByText('QQ group 654321 · bot 123456')).toBeTruthy();
    expect(screen.getByText('Scheduled QQ text message')).toBeTruthy();
    expect(screen.queryByText('no target')).toBeNull();
  });
  it('keeps retired records read-only while allowing canonical process edits', () => {
    const renderJob = (value: Job) => render(<JobDetailDrawer inline job={value} kindLabel="Background process" runs={[]} runsLoading={false}
      onLoadMoreRuns={vi.fn()} onClose={vi.fn()} onRunNow={vi.fn()} onTogglePaused={vi.fn()}
      onCancel={vi.fn()} onDelete={vi.fn()} onChangeTarget={vi.fn()} onEdit={vi.fn()} onToggleEntryEnabled={vi.fn()} />);
    const process: Job = { ...job, kind: 'background-process', status: 'running', argv: ['python', 'task.py'], cwd: 'owned', action: { api: 'shell', args: { command: '', cwd: 'owned' } } };
    const view = renderJob(process);
    expect(screen.getByRole('region', { name: 'Job detail' })).toBeTruthy();
    expect(screen.getByText('Immediate argv process (no shell)')).toBeTruthy();
    expect(screen.getByText(/task.py/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Cancel job' })).toBeTruthy();
    view.unmount();
    renderJob({ ...process, retirementStatus: 'Retired' });
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Cancel job' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
  });

});
