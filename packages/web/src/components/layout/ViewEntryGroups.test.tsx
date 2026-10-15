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

  it('floats the count at the bottom-right corner over the Jobs entry', () => {
    useJobStore.setState({
      jobs: [job(), job({ jobId: 'j2', status: 'running' }), job({ jobId: 'j3', status: 'completed' })],
    });
    renderNav();
    const badge = screen.getByTestId('jobs-running-badge');
    expect(badge.textContent).toBe('2');
    expect(badge.getAttribute('title')).toBe('2 job(s) running');
    // Floating overlay: absolutely positioned at the entry's bottom-right
    // corner, so it never occupies layout space nor resizes the entry.
    expect(badge.getAttribute('aria-hidden')).toBe('true');
    expect(badge.className).toContain('absolute');
    expect(badge.className).toContain('bottom-0');
    expect(badge.className).toContain('right-0');
    const link = screen.getByRole('link', { name: 'Jobs' });
    expect(badge.parentElement).toBe(link);
    expect(link.className).toContain('relative');
    // No reserved slot: the entry keeps its original single content row, and
    // the other entries keep the identical structure (no extra space).
    expect(link.firstElementChild!.textContent).toContain('Jobs');
    for (const name of ['Chat', 'Editor']) {
      const entry = screen.getByRole('link', { name });
      expect(entry.children).toHaveLength(1);
      expect(entry.querySelector('[data-testid="jobs-running-badge"]')).toBeNull();
    }
    fireEvent.click(link);
    expect(screen.getByText('jobs route marker')).toBeTruthy();
  });

  it('navigates when the floating badge itself is clicked', () => {
    useJobStore.setState({ jobs: [job()] });
    renderNav();
    fireEvent.click(screen.getByTestId('jobs-running-badge'));
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
