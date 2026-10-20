// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { WorkdirCleanup } from './DataSettingsPanel';
import { scanWorkdirs } from '@/services/api';

vi.mock('@/services/api', () => ({ scanWorkdirs: vi.fn() }));
const scan = vi.mocked(scanWorkdirs);
const candidate = { name: 'old', path: 'D:/temp/workdirs/old', modifiedAt: '2020-01-01', entries: 2 };
const response = { candidates: [candidate], results: [], scanned: 2, skipped: 1, deleted: 0, skipReasons: { referenced: 1 }, error: null };
beforeEach(() => scan.mockReset());
afterEach(cleanup);

it('warns about mtime uncertainty and requires valid days', () => {
  render(<WorkdirCleanup days={null} />);
  expect(screen.getByText(/may be inaccurate/).textContent).toContain('manual cleanup is recommended');
  expect((screen.getByRole('button', { name: 'Browse cleanup candidates' }) as HTMLButtonElement).disabled).toBe(true);
});

it('browses, selects all, deletes selected and shows individual failures', async () => {
  scan.mockResolvedValueOnce(response).mockResolvedValueOnce({ ...response, results: [{ name: 'old', deleted: false, reason: 'recently_modified' }] });
  render(<WorkdirCleanup days={30} />);
  fireEvent.click(screen.getByText('Browse cleanup candidates'));
  await screen.findByText(candidate.path);
  expect(screen.getByText(/Last modified:/).textContent).toContain('2020-01-01');
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select all' }));
  fireEvent.click(screen.getByText('Delete selected (1)'));
  await screen.findByText('old: Failed — recently_modified');
  expect(scan).toHaveBeenLastCalledWith(30, ['old']);
  expect(screen.getByText(candidate.path)).toBeTruthy();
});

it('removes successful rows and refreshes to the empty state', async () => {
  scan.mockResolvedValueOnce(response).mockResolvedValueOnce({ ...response, results: [{ name: 'old', deleted: true }] }).mockResolvedValueOnce({ ...response, candidates: [] });
  render(<WorkdirCleanup days={7} />);
  fireEvent.click(screen.getByText('Browse cleanup candidates'));
  await screen.findByText(candidate.path);
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select all' }));
  fireEvent.click(screen.getByText('Delete selected (1)'));
  await screen.findByText('old: Deleted');
  expect(screen.getByText('No eligible workdirs found.')).toBeTruthy();
  fireEvent.click(screen.getByText('Refresh'));
  await waitFor(() => expect(scan).toHaveBeenCalledTimes(3));
  expect(scan).toHaveBeenLastCalledWith(7, undefined);
});

it('shows request errors and allows retry', async () => {
  scan.mockRejectedValueOnce(new Error('unavailable')).mockResolvedValueOnce(response);
  render(<WorkdirCleanup days={30} />);
  fireEvent.click(screen.getByText('Browse cleanup candidates'));
  await screen.findByText('Cleanup failed: unavailable');
  fireEvent.click(screen.getByText('Refresh'));
  await screen.findByText(candidate.path);
  expect(screen.queryByText('Cleanup failed: unavailable')).toBeNull();
});
