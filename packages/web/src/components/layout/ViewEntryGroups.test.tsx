// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ViewEntryGroups } from './ViewEntryGroups';
import { useJobStore } from '@/stores/jobStore';
import type { Job } from '@/types/jobs';

const job = (overrides: Partial<Job> = {}): Job => ({
  jobId: 'job-1',
  kind: 'session-message',
  status: 'running',
  name: 'Job',
  description: '',
  source: { type: 'agent' },
  target: { sessionId: 'session-1' },
  paused: false,
  runCount: 0,
  createdAt: 1,
  updatedAt: 1,
  ...overrides,
}) as Job;

function renderNav() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <ViewEntryGroups isMobile={false} />
      <Routes>
        <Route path="/" element={<div>chat route marker</div>} />
        <Route path="/jobs" element={<div>jobs route marker</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ViewEntryGroups Jobs running badge', () => {
  afterEach(() => {
    cleanup();
    useJobStore.setState({
      jobs: [], loaded: false, loading: false, error: null, pinMetadataUnavailable: false,
    });
  });

  it('renders no badge when no job is running', () => {
    useJobStore.setState({
      jobs: [job({ status: 'completed' }), job({ jobId: 'j2', status: 'pending' }), job({ jobId: 'j3', status: 'starting' })],
    });
    renderNav();
    expect(screen.queryByTestId('jobs-running-badge')).toBeNull();
    expect(screen.getByRole('link', { name: 'Jobs' })).toBeTruthy();
  });

  it('shows the running count on the Jobs entry without breaking its accessible name', () => {
    useJobStore.setState({
      jobs: [job(), job({ jobId: 'j2', status: 'running' }), job({ jobId: 'j3', status: 'completed' })],
    });
    renderNav();
    const badge = screen.getByTestId('jobs-running-badge');
    expect(badge.textContent).toBe('2');
    expect(badge.getAttribute('title')).toBe('2 job(s) running');
    // The entry keeps its name and remains a normal navigation link.
    const link = screen.getByRole('link', { name: 'Jobs' });
    expect(link.contains(badge)).toBe(true);
    fireEvent.click(link);
    expect(screen.getByText('jobs route marker')).toBeTruthy();
  });

  it('caps the count at 99+', () => {
    useJobStore.setState({
      jobs: Array.from({ length: 120 }, (_, index) => job({ jobId: `j${index}` })),
    });
    renderNav();
    expect(screen.getByTestId('jobs-running-badge').textContent).toBe('99+');
  });

  it('hides the badge again when the last running job settles', () => {
    useJobStore.setState({ jobs: [job(), job({ jobId: 'j2', status: 'running' })] });
    const { rerender } = renderNav();
    expect(screen.getByTestId('jobs-running-badge').textContent).toBe('2');

    useJobStore.setState({
      jobs: [job({ status: 'completed' }), job({ jobId: 'j2', status: 'failed' })],
    });
    rerender(
      <MemoryRouter initialEntries={['/']}>
        <ViewEntryGroups isMobile={false} />
        <Routes>
          <Route path="/jobs" element={<div>jobs route marker</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.queryByTestId('jobs-running-badge')).toBeNull();
  });
});
