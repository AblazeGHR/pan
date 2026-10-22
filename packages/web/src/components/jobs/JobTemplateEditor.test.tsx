// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { JobTemplateEditor } from './JobTemplateEditor';
import { deleteCompleteJobTemplate, fetchCompleteJobTemplates, instantiateCompleteJobTemplate, saveCompleteJobTemplate } from '@/services/api';

vi.mock('@/services/api', async (original) => ({ ...await original<typeof import('@/services/api')>(),
  fetchCompleteJobTemplates: vi.fn(), instantiateCompleteJobTemplate: vi.fn(), deleteCompleteJobTemplate: vi.fn(), saveCompleteJobTemplate: vi.fn() }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const template = { id: 'jtpl_fixed', name: 'Fixed template', revision: 7, enabled: true,
  config: { name: 'fixed', actionId: 'a', arguments: {}, schedule: [], trigger: { kind: 'immediate' as const }, enabled: false } };

async function setup() {
  vi.mocked(fetchCompleteJobTemplates).mockResolvedValue([template]);
  const instantiated = vi.fn();
  render(<JobTemplateEditor actions={[]} submitting={false} onCreate={vi.fn()} onInstantiated={instantiated} />);
  await screen.findByRole('option', { name: /Fixed template/ });
  fireEvent.change(screen.getByLabelText('Complete Template'), { target: { value: template.id } });
  return instantiated;
}

it('fixed instantiate reuses idempotency key after an unknown result and never overlays editor fields', async () => {
  const onInstantiated = await setup();
  vi.mocked(instantiateCompleteJobTemplate).mockRejectedValue(new Error('unknown receipt'));
  fireEvent.click(screen.getByRole('button', { name: 'Instantiate fixed Template' }));
  await screen.findByText('unknown receipt');
  fireEvent.click(screen.getByRole('button', { name: 'Instantiate fixed Template' }));
  await waitFor(() => expect(instantiateCompleteJobTemplate).toHaveBeenCalledTimes(2));
  expect(onInstantiated).not.toHaveBeenCalled();
  const calls = vi.mocked(instantiateCompleteJobTemplate).mock.calls;
  expect(calls[0]).toEqual(calls[1]);
  expect(calls[0]?.[0]).toEqual(template);
});

it('dirty configuration cannot be used as an instantiate overlay', async () => {
  await setup();
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'different intent' } });
  expect((screen.getByRole('button', { name: 'Instantiate fixed Template' }) as HTMLButtonElement).disabled).toBe(true);
  expect(instantiateCompleteJobTemplate).not.toHaveBeenCalled();
});

it('template deletion uses the selected revision only after explicit confirmation', async () => {
  await setup();
  fireEvent.change(screen.getByLabelText('Create'), { target: { value: 'template' } });
  const confirmation = vi.spyOn(window, 'confirm').mockReturnValue(false);
  fireEvent.click(screen.getByRole('button', { name: 'Delete complete Template' }));
  expect(deleteCompleteJobTemplate).not.toHaveBeenCalled();
  confirmation.mockReturnValue(true);
  vi.mocked(deleteCompleteJobTemplate).mockResolvedValue();
  fireEvent.click(screen.getByRole('button', { name: 'Delete complete Template' }));
  await waitFor(() => expect(deleteCompleteJobTemplate).toHaveBeenCalledWith(template));
  confirmation.mockRestore();
});

it('saves Template capability independently from disabled copied Job execution and rotates key only on confirmed revision', async () => {
  await setup();
  vi.mocked(instantiateCompleteJobTemplate).mockRejectedValue(new Error('unknown receipt'));
  fireEvent.click(screen.getByRole('button', { name: 'Instantiate fixed Template' }));
  await screen.findByText('unknown receipt');
  const originalKey = vi.mocked(instantiateCompleteJobTemplate).mock.calls[0]?.[1];
  fireEvent.change(screen.getByLabelText('Create'), { target: { value: 'template' } });
  vi.mocked(saveCompleteJobTemplate).mockResolvedValue({ ...template, revision: 8 });
  fireEvent.click(screen.getByRole('button', { name: 'Save complete Template' }));
  await waitFor(() => expect(saveCompleteJobTemplate).toHaveBeenCalledOnce());
  expect(vi.mocked(saveCompleteJobTemplate).mock.calls[0]?.[0]).toMatchObject({ enabled: true, config: { enabled: false } });
  await waitFor(() => expect(screen.getByText(/Instantiate copies revision 8/)).toBeTruthy());
  fireEvent.click(screen.getByRole('button', { name: 'Instantiate fixed Template' }));
  await waitFor(() => expect(instantiateCompleteJobTemplate).toHaveBeenCalledTimes(2));
  expect(vi.mocked(instantiateCompleteJobTemplate).mock.calls[1]?.[1]).not.toBe(originalKey);
});
