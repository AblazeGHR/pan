// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { InteractionUnits } from './InteractionUnits';
import type { InteractionCard } from './lockerModel';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('disables an execution option without trusted bindable capability and explains it', () => {
  const card = {
    id: 'card',
    schemaVersion: 3,
    status: 'open',
    revision: 0,
    definition: { title: 'Card', body: '', summaryBody: '' },
    units: [
      {
        id: 'q',
        prompt: 'Choose',
        type: 'single_choice',
        state: 'pending',
        choices: [
          {
            id: 'go',
            label: 'Unavailable fixed Action',
            actionRef: { id: 'runtime', revision: 1, digest: 'f'.repeat(64) },
          },
        ],
      },
    ],
    unitStatus: 'pending',
    delivery: { state: 'not_requested' },
    response: null,
  } as InteractionCard;
  render(<InteractionUnits card={card} update={vi.fn()} />);
  expect(
    (screen.getByRole('radio', { name: 'Unavailable fixed Action' }) as HTMLInputElement).disabled,
  ).toBe(true);
  expect(screen.getByText(/缺少受信可绑定能力/)).toBeTruthy();
});

it('submits one rejection without BTW and keeps the other unit editable', async () => {
  const card = {
    id: 'card',
    schemaVersion: 3,
    status: 'open',
    revision: 0,
    definition: { title: 'Card', body: '', summaryBody: '' },
    units: [
      { id: 'a', prompt: 'First', type: 'text', state: 'pending' },
      {
        id: 'b',
        prompt: 'Second',
        type: 'single_choice',
        state: 'pending',
        choices: [{ id: 'yes', label: 'Yes' }],
      },
    ],
    unitStatus: 'pending',
    delivery: { state: 'not_requested' },
    response: null,
  } as InteractionCard;
  const next = {
    ...card,
    revision: 1,
    status: 'partial',
    unitStatus: 'partial',
    units: [{ ...card.units![0], state: 'rejected', answer: { rejected: true } }, card.units![1]],
  } as InteractionCard;
  const fetch = vi
    .fn<typeof globalThis.fetch>()
    .mockResolvedValue({ ok: true, json: async () => ({ card: next }) } as Response);
  vi.stubGlobal('fetch', fetch);
  const update = vi.fn();
  const view = render(<InteractionUnits card={card} update={update} />);
  const first = screen.getByRole('group', { name: /First/ });
  fireEvent.click(within(first).getByRole('button', { name: '拒绝回答此问题' }));
  fireEvent.click(within(first).getByRole('button', { name: '提交此问题' }));
  await waitFor(() => expect(update).toHaveBeenCalledWith(next));
  expect(JSON.parse(String(fetch.mock.calls[0]?.[1]?.body)).unitAnswers).toEqual({
    a: { rejected: true },
  });
  view.rerender(<InteractionUnits card={next} update={update} />);
  expect(within(screen.getByRole('group', { name: /First/ })).queryByRole('textbox')).toBeNull();
  expect((screen.getByRole('radio', { name: 'Yes' }) as HTMLInputElement).disabled).toBe(false);
  expect(screen.getByText(/BTW 不会修改或阻止/)).toBeTruthy();
});
