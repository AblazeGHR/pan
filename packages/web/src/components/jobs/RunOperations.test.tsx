// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RunList } from '@/views/CanonicalJobsView';
import { canonicalJobs } from '@/services/canonicalJobs';

beforeEach(() => sessionStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('keeps the original Run operation identity after a lost response and queries without resending', async () => {
  const job = { id: 'owned-job', name: 'Owned', state: 'enabled', stateRevision: 2, configRevision: 4 };
  const run = { runId: 'owned-run', jobId: 'owned-job', configRevision: 4, state: 'failed', stateRevision: 3 };
  vi.spyOn(canonicalJobs, 'runs').mockResolvedValue({ items: [run], nextCursor: null });
  vi.spyOn(canonicalJobs, 'get').mockResolvedValue(job);
  const write = vi.spyOn(canonicalJobs, 'operate').mockRejectedValue(new TypeError('lost response'));
  const lookup = vi.spyOn(canonicalJobs, 'lookup').mockImplementation(async (_operation, key) => ({ operationId: 'original-op', requestKey: key, status: 'completed', objectRefs: [], links: {} }));
  const first = render(<RunList job={job} onClose={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'clone-and-run' }));
  fireEvent.change(screen.getByLabelText('New name (optional)'), { target: { value: 'Owned copy' } });
  expect(screen.getByText('Advanced run parameters').closest('details')?.open).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Confirm operation' }));
  await screen.findByText('The result is unconfirmed. Query the original identity.');
  await waitFor(() => expect(write).toHaveBeenCalledOnce());
  expect(write.mock.calls[0]).toEqual([run, 'clone-and-run', 4, expect.any(String), { overrides: { name: 'Owned copy' } }, '']);
  const key = write.mock.calls[0]![3];
  first.unmount();
  render(<RunList job={job} onClose={vi.fn()} />);
  expect(await screen.findByText('The result is unconfirmed. Query the original identity.')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Query original operation' }));
  await waitFor(() => expect(lookup).toHaveBeenCalledWith('clone-and-run', key, undefined));
  expect(write).toHaveBeenCalledOnce();
});
