// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { SchedulerInstantInput } from './SchedulerInstantInput';
import { canonicalJobs, type TimeInputResolution } from '@/services/canonicalJobs';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function Input({ initial = '' }: { initial?: string }) {
  const [at, setAt] = useState(initial);
  return (
    <>
      <SchedulerInstantInput label="Local time" value={at} onChange={setAt} />
      <output data-testid="at">{at}</output>
    </>
  );
}
const input = () => screen.getByLabelText('Local time', { exact: true }) as HTMLInputElement;
const at = () => screen.getByTestId('at').textContent;

it('resolves plain host-local input through Pan without browser Date conversion', async () => {
  const resolve = vi
    .spyOn(canonicalJobs, 'resolveTimeInput')
    .mockResolvedValue({
      status: 'resolved',
      at: '2027-01-02T14:30:12.123456789Z',
      timezone: 'America/New_York',
      zoneSelection: 'host',
    });
  render(<Input />);
  fireEvent.change(input(), { target: { value: '2027-01-02 09:30:12.123456789' } });
  expect(input().checkValidity()).toBe(false);
  await waitFor(() => expect(at()).toBe('2027-01-02T14:30:12.123456789Z'));
  expect(resolve.mock.calls[0]!.slice(0, 2)).toEqual(['2027-01-02 09:30:12.123456789', undefined]);
  expect(input().value).toBe('2027-01-02 09:30:12.123456789');
  expect(input().checkValidity()).toBe(true);
});

it('keeps a gap invalid and requires choosing either actual instant for a fold', async () => {
  const resolve = vi.spyOn(canonicalJobs, 'resolveTimeInput');
  resolve.mockResolvedValueOnce({
    status: 'nonexistent',
    reason: 'dst_gap',
    timezone: 'America/New_York',
  });
  render(<Input />);
  fireEvent.change(input(), { target: { value: '2026-03-08 02:30' } });
  await screen.findByRole('alert');
  expect(at()).toBe('');
  expect(input().checkValidity()).toBe(false);
  resolve.mockResolvedValueOnce({
    status: 'ambiguous',
    timezone: 'America/New_York',
    options: [
      { at: '2026-11-01T05:30:00.123456789Z', localTime: '2026-11-01T01:30:00.123456789-04:00' },
      { at: '2026-11-01T06:30:00.123456789Z', localTime: '2026-11-01T01:30:00.123456789-05:00' },
    ],
  });
  fireEvent.change(input(), { target: { value: '2026-11-01 01:30:00.123456789' } });
  const choices = await screen.findAllByRole('button');
  expect(choices).toHaveLength(2);
  expect(at()).toBe('');
  expect(input().checkValidity()).toBe(false);
  fireEvent.click(choices[1]!);
  expect(at()).toBe('2026-11-01T06:30:00.123456789Z');
  expect(input().checkValidity()).toBe(true);
});

it('preserves an existing offset timestamp and accepts explicit edits without host resolution', () => {
  const resolve = vi.spyOn(canonicalJobs, 'resolveTimeInput');
  render(<Input initial="2027-01-01T09:00:12.123456789+05:30" />);
  expect(input().value).toBe('2027-01-01T09:00:12.123456789+05:30');
  expect(at()).toBe(input().value);
  fireEvent.change(input(), { target: { value: '2027-01-01T09:00:13.123456789+05:30' } });
  expect(at()).toBe('2027-01-01T09:00:13.123456789+05:30');
  expect(resolve).not.toHaveBeenCalled();
});

it('uses the optional server zone override and never saves unavailable results', async () => {
  const resolve = vi
    .spyOn(canonicalJobs, 'resolveTimeInput')
    .mockResolvedValue({ status: 'unavailable' });
  render(<Input />);
  fireEvent.change(screen.getByLabelText('Local time timezone override'), {
    target: { value: 'Unknown/Zone' },
  });
  fireEvent.change(input(), { target: { value: '2027-01-01 09:00' } });
  await screen.findByRole('alert');
  expect(resolve.mock.calls.at(-1)!.slice(0, 2)).toEqual(['2027-01-01 09:00', 'Unknown/Zone']);
  expect(at()).toBe('');
  expect(input().checkValidity()).toBe(false);
});

it('ignores a stale host resolution after a newer draft was resolved', async () => {
  let finish!: (result: TimeInputResolution) => void;
  vi.spyOn(canonicalJobs, 'resolveTimeInput')
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce({ status: 'resolved', at: '2027-01-02T10:00:00Z' });
  render(<Input />);
  fireEvent.change(input(), { target: { value: '2027-01-01 09:00' } });
  fireEvent.change(input(), { target: { value: '2027-01-02 10:00' } });
  await waitFor(() => expect(at()).toBe('2027-01-02T10:00:00Z'));
  await act(async () => {
    finish({ status: 'resolved', at: '2027-01-01T09:00:00Z' });
  });
  expect(at()).toBe('2027-01-02T10:00:00Z');
});
