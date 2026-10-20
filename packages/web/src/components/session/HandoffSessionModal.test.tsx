// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HandoffSessionModal } from './HandoffSessionModal';
import { handoffSession } from '@/services/api';
import { useSessionStore } from '@/stores/sessionStore';
import type { Session } from '@/types';

vi.mock('@/services/api', async (original) => ({
  ...(await original<typeof import('@/services/api')>()),
  handoffSession: vi.fn(),
}));
const a: Session = {
  id: 'a',
  name: 'Alpha',
  history: [],
  alwaysThinkingEnabled: false,
  effort: '',
};
const b: Session = { ...a, id: 'b', name: 'Bravo' };
const submit = () => fireEvent.click(screen.getByRole('button', { name: 'Confirm handoff' }));
const brief = () =>
  screen.getByRole('textbox', { name: 'Handoff brief (required)' }) as HTMLTextAreaElement;
const original = () => screen.getByRole('textbox', { name: 'New original prompt' }) as HTMLTextAreaElement;
const toggle = () => fireEvent.click(screen.getByRole('checkbox'));
beforeEach(() => {
  vi.mocked(handoffSession).mockReset();
  useSessionStore.setState({ loadSessions: vi.fn().mockResolvedValue(undefined) });
});
afterEach(cleanup);

it('requires a brief and defaults to inheritance, ignoring a disabled residual draft', async () => {
  vi.mocked(handoffSession).mockRejectedValue(new Error('try again'));
  render(<HandoffSessionModal session={a} onClose={() => {}} />);
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  expect(original().disabled).toBe(true);
  expect((screen.getByRole('button', { name: 'Confirm handoff' }) as HTMLButtonElement).disabled).toBe(
    true,
  );
  fireEvent.change(brief(), { target: { value: 'brief' } });
  toggle();
  fireEvent.change(original(), { target: { value: 'residual' } });
  toggle();
  submit();
  await screen.findByRole('alert');
  expect(handoffSession).toHaveBeenCalledWith('a', {
    handoffPrompt: 'brief',
    copySettings: true,
    overrideOriginalPrompt: false,
  });
  expect(brief().value).toBe('brief');
  toggle();
  expect(original().value).toBe('residual');
});

it.each(['new rules', ''])(
  'submits an explicit override including empty text: %j',
  async (text) => {
    vi.mocked(handoffSession).mockRejectedValue(new Error('failure'));
    render(<HandoffSessionModal session={a} onClose={() => {}} />);
    fireEvent.change(brief(), { target: { value: 'brief' } });
    toggle();
    fireEvent.change(original(), { target: { value: text } });
    submit();
    await screen.findByRole('alert');
    expect(handoffSession).toHaveBeenCalledWith('a', {
      handoffPrompt: 'brief',
      copySettings: true,
      overrideOriginalPrompt: true,
      originalPrompt: text,
    });
    expect(original().value).toBe(text);
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true);
  },
);

it('resets on reopening and Session changes but retains drafts across same Session refreshes', () => {
  const onClose = vi.fn();
  const view = render(<HandoffSessionModal session={a} onClose={onClose} />);
  fireEvent.change(brief(), { target: { value: 'draft' } });
  toggle();
  fireEvent.change(original(), { target: { value: 'private rules' } });
  view.rerender(<HandoffSessionModal session={{ ...a }} onClose={onClose} />);
  expect(original().value).toBe('private rules');
  view.rerender(<HandoffSessionModal session={b} onClose={onClose} />);
  expect(brief().value).toBe('');
  expect(original().value).toBe('');
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  view.rerender(<HandoffSessionModal session={null} onClose={onClose} />);
  view.rerender(<HandoffSessionModal session={a} onClose={onClose} />);
  expect(brief().value).toBe('');
  expect(original().value).toBe('');
  expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
});

it.each(['resolve', 'reject'])(
  'ignores late %s from a closed dialog after opening another Session',
  async (outcome) => {
    let resolve!: (value: Awaited<ReturnType<typeof handoffSession>>) => void;
    let reject!: (error: Error) => void;
    vi.mocked(handoffSession).mockImplementation(
      () =>
        new Promise((yes, no) => {
          resolve = yes;
          reject = no;
        }),
    );
    const onClose = vi.fn();
    const view = render(<HandoffSessionModal session={a} onClose={onClose} />);
    fireEvent.change(brief(), { target: { value: 'brief a' } });
    submit();
    view.rerender(<HandoffSessionModal session={null} onClose={onClose} />);
    view.rerender(<HandoffSessionModal session={b} onClose={onClose} />);
    fireEvent.change(brief(), { target: { value: 'brief b' } });
    await act(async () => {
      if (outcome === 'resolve') resolve({ ok: true, session: b, archivedSession: a });
      else reject(new Error('late failure'));
    });
    expect(brief().value).toBe('brief b');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    expect(useSessionStore.getState().loadSessions).not.toHaveBeenCalled();
  },
);

it('closes a successful submission and refreshes the list exactly once', async () => {
  vi.mocked(handoffSession).mockResolvedValue({ ok: true, session: b, archivedSession: a });
  const onClose = vi.fn();
  render(<HandoffSessionModal session={a} onClose={onClose} />);
  fireEvent.change(brief(), { target: { value: 'brief' } });
  submit();
  await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  expect(useSessionStore.getState().loadSessions).toHaveBeenCalledTimes(1);
});
