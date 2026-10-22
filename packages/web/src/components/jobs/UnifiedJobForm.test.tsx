// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApiRequestError } from '@/services/api';
import type { Action } from '@/types/actions';
import { UnifiedJobForm } from './UnifiedJobForm';

afterEach(cleanup);
const action: Action = { id: 'read_sessions', name: 'Read sessions', description: 'test', enabled: true,
  effect: 'read', output_format: 'json', input_schema: { type: 'object', properties: {}, additionalProperties: false },
  execution: { kind: 'api', method: 'GET', path: '/api/sessions' } };

function form(onSubmit = vi.fn()) {
  render(<UnifiedJobForm actions={[action]} initial={{ name: 'Unified', actionId: action.id }} expectedRevision={7} onSubmit={onSubmit} />);
  return onSubmit;
}

describe('Unified Job configuration editor', () => {
  it('creates from Action reference with conservative explicit policies, without kind selector', async () => {
    const submit = form();
    expect(screen.queryByLabelText('Job kind')).toBeNull();
    expect(screen.getByText(/revision 7/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save unified Job' }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    expect(submit.mock.calls[0]?.[0]).toMatchObject({ actionId: action.id, arguments: {}, enabled: false,
      completion: { kind: 'ordinary' }, recovery: { enabled: false }, overlap: { mode: 'forbid', parallelLimit: 3 }, notification: { target: 'job' } });
  });
  it('refuses extra body for an empty Action schema and keeps the draft', async () => {
    const submit = form();
    fireEvent.change(screen.getByLabelText('Declared arguments (JSON)'), { target: { value: '{"text":"injected"}' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save unified Job' }));
    expect(await screen.findByText('Arguments contain undeclared Action fields')).toBeTruthy();
    expect(submit).not.toHaveBeenCalled();
    expect(screen.getByText(/Unsaved changes/)).toBeTruthy();
  });
  it('rejects malformed query instead of keeping an earlier valid query', async () => {
    const submit = form();
    fireEvent.change(screen.getByLabelText('Trigger'), { target: { value: 'event' } });
    fireEvent.change(screen.getByLabelText('Event query (JSON)'), { target: { value: '{' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save unified Job' }));
    await waitFor(() => expect(submit).not.toHaveBeenCalled());
    expect(screen.getByText(/Unsaved changes/)).toBeTruthy();
  });
  it('requires monitoring declaration before recovery and persists backoff/reset fields', async () => {
    const submit = form();
    expect((screen.getByLabelText('Recovery') as HTMLSelectElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Completion'), { target: { value: 'monitoring' } });
    fireEvent.change(screen.getByLabelText('Recovery'), { target: { value: 'on' } });
    fireEvent.change(screen.getByLabelText('Stable running reset (seconds)'), { target: { value: '600' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save unified Job' }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    expect(submit.mock.calls[0]?.[0]?.recovery).toMatchObject({ enabled: true, monitoring: true, stableResetSec: 600, backoffSec: [5, 15, 60] });
  });
  it.each([401, 409])('keeps dirty inputs after HTTP %s', async (status) => {
    const submit = vi.fn().mockRejectedValue(new ApiRequestError(status, 'rejected'));
    form(submit);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Retained draft' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save unified Job' }));
    expect(await screen.findByText(new RegExp(`${status}:`))).toBeTruthy();
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Retained draft');
    expect(screen.getByText(/Unsaved changes/)).toBeTruthy();
  });
});
