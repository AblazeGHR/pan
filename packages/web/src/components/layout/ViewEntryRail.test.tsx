// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ViewEntryRail } from './ViewEntryRail';
import { entries } from './viewEntries';
import { useJobStore } from '@/stores/jobStore';
import type { Job } from '@/types/jobs';

const job = (overrides: Partial<Job> = {}): Job =>
  ({
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

function renderRail(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ViewEntryRail />
      <Routes>
        <Route path="/" element={<div>chat route marker</div>} />
        <Route path="/editor" element={<div>editor route marker</div>} />
        <Route path="/jobs" element={<div>jobs route marker</div>} />
        <Route path="/terminals" element={<div>terminals route marker</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ViewEntryRail (desktop collapsed sidebar)', () => {
  afterEach(() => {
    cleanup();
    useJobStore.setState({
      jobs: [],
      loaded: false,
      loading: false,
      error: null,
      pinMetadataUnavailable: false,
    });
  });

  it('renders one vertical entry per shared view entry — no second route list', () => {
    renderRail('/');
    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(entries.length);
    for (const entry of entries) {
      const link = screen.getByRole('link', { name: entry.label });
      // Tooltip + accessible name both present; href matches the shared route.
      expect(link.getAttribute('title')).toBe(entry.label);
      expect(link.getAttribute('href')).toBe(entry.to);
    }
  });

  it('marks the active route and navigates on click', () => {
    renderRail('/');
    expect(screen.getByRole('link', { name: 'Chat' }).className).toContain('text-accent');
    expect(screen.getByRole('link', { name: 'Editor' }).className).not.toContain('text-accent');

    fireEvent.click(screen.getByRole('link', { name: 'Editor' }));
    expect(screen.getByText('editor route marker')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Editor' }).className).toContain('text-accent');

    fireEvent.click(screen.getByRole('link', { name: 'Jobs' }));
    expect(screen.getByText('jobs route marker')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Jobs' }).className).toContain('text-accent');

    fireEvent.click(screen.getByRole('link', { name: 'Terminal' }));
    expect(screen.getByText('terminals route marker')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Terminal' }).className).toContain('text-accent');

    fireEvent.click(screen.getByRole('link', { name: 'Chat' }));
    expect(screen.getByText('chat route marker')).toBeTruthy();
  });

  it('matches / exactly so nested routes never light up Chat', () => {
    renderRail('/jobs');
    expect(screen.getByRole('link', { name: 'Jobs' }).className).toContain('text-accent');
    expect(screen.getByRole('link', { name: 'Chat' }).className).not.toContain('text-accent');
  });

  it('shows the running-jobs badge on the Jobs entry only', () => {
    useJobStore.setState({
      jobs: [
        job(),
        job({ jobId: 'j2', status: 'running' }),
        job({ jobId: 'j3', status: 'completed' }),
      ],
    });
    renderRail('/');
    const badge = screen.getByTestId('jobs-running-badge');
    expect(badge.textContent).toBe('2');
    const jobsLink = screen.getByRole('link', { name: 'Jobs' });
    expect(badge.parentElement).toBe(jobsLink);
    for (const name of ['Chat', 'Editor', 'Terminal']) {
      expect(
        screen.getByRole('link', { name }).querySelector('[data-testid="jobs-running-badge"]'),
      ).toBeNull();
    }
  });

  it('renders no badge when no job is running', () => {
    useJobStore.setState({ jobs: [job({ status: 'completed' })] });
    renderRail('/');
    expect(screen.queryByTestId('jobs-running-badge')).toBeNull();
  });
});
