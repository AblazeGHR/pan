// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import LockerView from './LockerView';
import { lockerProjection, type InteractionCard, type Locker } from './lockerModel';
vi.mock('@/components/chat/MarkdownRenderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}));
vi.mock('@/components/automation/JobUserAuthorization', () => ({
  JobUserAuthorization: () => null,
}));
vi.mock('@/stores/workspaceStore', () => {
  const state = { workspaces: [{ id: 'w', name: 'Work' }], loaded: true };
  const useWorkspaceStore = Object.assign(
    (select: (value: typeof state) => unknown) => select(state),
    { getState: () => ({ loadWorkspaces: vi.fn() }) },
  );
  return { useWorkspaceStore };
});
const uiState = { activeWorkspaceId: 'all', setMobileSidebarOpen: vi.fn() };
vi.mock('@/stores/uiStore', () => {
  const useUIStore = Object.assign(
    (select: (value: typeof uiState) => unknown) => select(uiState),
    { getState: () => uiState },
  );
  return { useUIStore };
});
const lockers: Locker[] = [
  {
    id: 'l1',
    sessionId: 'ses_one',
    sourceName: 'Source One',
    workspaceIds: ['w'],
    available: true,
    total: 1,
    unread: 1,
  },
  {
    id: 'l2',
    sessionId: 'ses_two',
    sourceName: 'Source Two',
    workspaceIds: [],
    available: false,
    total: 1,
    unread: 1,
  },
];
function card(id = 'a', lockerId = 'l1'): InteractionCard {
  return {
    id,
    lockerId,
    ownerSessionId: lockerId === 'l1' ? 'ses_one' : 'ses_two',
    createdAt: `2026-10-23T0${id === 'a' ? '1' : '2'}:00:00Z`,
    read: false,
    revision: 0,
    status: 'open',
    definition: {
      title: `Card ${id}`,
      body: '**Body**',
      summaryBody: 'Summary',
      answerInput: { label: 'Reply' },
      choices: [{ id: 'go', label: 'Go', triggerRef: { id: 'existing-trigger' } }],
    },
    trustedChoices: {
      go: {
        summary: 'pause_everything 路 trusted Action',
        targetIds: ['ses_target'],
        actionId: 'pause',
        actionDigest: 'a'.repeat(64),
        triggerRevision: 1,
        arguments: {},
      },
    },
    response: null,
    delivery: { state: 'not_requested' },
  };
}
let cards: InteractionCard[];
let calls: { path: string; body: Record<string, unknown>; method?: string }[];
beforeEach(() => {
  uiState.activeWorkspaceId = 'all';
  cards = [card(), card('b', 'l2')];
  calls = [];
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body ?? '{}'));
      calls.push({ path, body, method: init?.method });
      if (path === '/api/user-lockers') return { ok: true, json: async () => ({ lockers }) };
      if (path.startsWith('/api/user-inbox?'))
        return { ok: true, json: async () => ({ cards, nextAfter: null }) };
      const id = path.split('/')[3];
      const index = cards.findIndex((item) => item.id === id);
      const previous = cards[index];
      if (!previous) throw new Error(`Unknown test Card ${id}`);
      if (init?.method === 'DELETE') {
        cards = cards.filter((item) => item.id !== id);
        return { ok: true, json: async () => ({ deleted: true }) };
      }
      if (path.endsWith('/submit') || path.endsWith('/reject'))
        cards[index] = {
          ...previous,
          revision: 1,
          status: path.endsWith('/reject') ? 'rejected' : 'responded',
          response: {
            outcome: path.endsWith('/reject') ? 'reject' : 'submit',
            answer: body.answer as string | undefined,
            choiceId: body.choiceId as string | undefined,
          },
        };
      if (path.endsWith('/read')) cards[index] = { ...previous, read: body.read as boolean };
      return { ok: true, json: async () => ({ card: cards[index] }) };
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount() {
  return render(
    <MemoryRouter>
      <LockerView />
    </MemoryRouter>,
  );
}
describe('Locker panel', () => {
  it('projects original Cards by Workspace without an aggregate container', () => {
    expect(
      lockerProjection(lockers, [card('b', 'l2'), card()], '', 'aggregate').map((item) => item.id),
    ).toEqual(['a', 'b']);
    expect(
      lockerProjection(lockers, [card('b', 'l2'), card()], 'w', 'aggregate').map((item) => item.id),
    ).toEqual(['a']);
    expect(
      lockerProjection(lockers, [card('b', 'l2'), card()], 'w', 'l1').map((item) => item.id),
    ).toEqual(['a']);
  });
  it('uses the existing WorkspaceRail scope and renders source Locker actions', async () => {
    mount();
    await screen.findByRole('heading', { name: 'Card a' });
    expect(screen.queryByLabelText('Workspace')).toBeNull();
    expect(screen.getByRole('main', { name: 'Locker' })).toBeTruthy();
    expect(
      within(screen.getByTestId('locker-source-l1')).getByRole('button', {
        name: 'Navigate to chat',
      }),
    ).not.toHaveProperty('disabled', true);
    expect(
      within(screen.getByTestId('locker-source-l1')).getByRole('button', { name: 'Read all' }),
    ).not.toHaveProperty('disabled', true);
    expect(screen.getByTestId('locker-drag-l1')).toBeTruthy();
    expect(
      within(screen.getByTestId('locker-source-l2')).getByRole('button', {
        name: 'Navigate to chat',
      }),
    ).toHaveProperty('disabled', true);
  });
  it('shows trusted choice meaning and disables an unbound choice', async () => {
    cards[1] = { ...card('b', 'l2'), trustedChoices: {} };
    mount();
    await screen.findByRole('heading', { name: 'Card a' });
    expect(screen.getByText('pause_everything 路 trusted Action')).toBeTruthy();
    expect(screen.getAllByRole('radio', { name: 'Go' })[1]).toHaveProperty('disabled', true);
  });
  it('submits answer and choice, freezes the card, then deletes the completed record', async () => {
    mount();
    await screen.findByRole('heading', { name: 'Card a' });
    fireEvent.change(screen.getAllByLabelText('Reply (optional)')[0]!, {
      target: { value: 'Approved' },
    });
    fireEvent.click(screen.getAllByRole('radio', { name: 'Go' })[0]!);
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]!);
    await waitFor(() => expect(screen.getAllByText('Submitted').length).toBeGreaterThan(0));
    expect(calls.find((call) => call.path.endsWith('/submit'))?.body).toMatchObject({
      answer: 'Approved',
      choiceId: 'go',
      expectedRevision: 0,
    });
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete record' })[0]!);
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Card a' })).toBeNull());
  });
  it('rejects with an optional reason and preserves the reason as read-only response', async () => {
    mount();
    await screen.findByRole('heading', { name: 'Card a' });
    fireEvent.click(screen.getAllByRole('button', { name: 'Reject' })[0]!);
    fireEvent.change(screen.getByLabelText('Reason for rejection'), {
      target: { value: 'Keep the current scope.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm rejection' }));
    await waitFor(() => expect(calls.some((call) => call.path.endsWith('/reject'))).toBe(true));
    expect(calls.find((call) => call.path.endsWith('/reject'))?.body).toMatchObject({
      answer: 'Keep the current scope.',
    });
    expect(screen.getByText('Keep the current scope.')).toBeTruthy();
  });
  it('Read all updates original source cards through the read API', async () => {
    mount();
    await screen.findByRole('heading', { name: 'Card a' });
    fireEvent.click(
      within(screen.getByTestId('locker-source-l1')).getByRole('button', { name: 'Read all' }),
    );
    await waitFor(() =>
      expect(calls.filter((call) => call.path.endsWith('/read')).length).toBeGreaterThan(0),
    );
    expect(
      calls.filter((call) => call.path.endsWith('/read')).every((call) => call.body.read === true),
    ).toBe(true);
  });
  it('retains a draft across source filters and applies terminal server response on refresh', async () => {
    mount();
    await screen.findByRole('heading', { name: 'Card a' });
    fireEvent.change(screen.getAllByLabelText('Reply (optional)')[0]!, {
      target: { value: 'local draft' },
    });
    fireEvent.click(screen.getByTestId('locker-source-l2'));
    fireEvent.click(screen.getByTestId('locker-source-l1'));
    expect(screen.getByLabelText('Reply (optional)')).toHaveProperty('value', 'local draft');
    cards[0] = {
      ...cards[0]!,
      status: 'responded',
      revision: 1,
      response: { answer: 'server answer' },
    };
    fireEvent.click(screen.getByRole('button', { name: 'Refresh Locker' }));
    await screen.findByText('server answer');
    expect(screen.queryByRole('textbox', { name: 'Reply (optional)' })).toBeNull();
  });
  it('loads every cursor page without silently truncating the inbox', async () => {
    const original = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (...args: Parameters<typeof fetch>) => {
        if (String(args[0]).startsWith('/api/user-inbox?')) {
          const last = String(args[0]).includes('after=');
          return {
            ok: true,
            json: async () => ({
              cards: [last ? card('b', 'l2') : card()],
              nextAfter: last ? null : 'cursor/2',
            }),
          } as Response;
        }
        return original(...args);
      }),
    );
    mount();
    await screen.findByRole('heading', { name: 'Card b' });
    expect(screen.getByRole('heading', { name: 'Card a' })).toBeTruthy();
    expect(fetch).toHaveBeenCalledWith(
      '/api/user-inbox?limit=500&after=cursor%2F2',
      expect.anything(),
    );
  });
  it('keeps UserAuth failure visible and does not show cards', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 503,
        json: async () => ({ detail: { code: 'user_auth_unavailable' } }),
      })),
    );
    mount();
    expect((await screen.findByRole('alert')).textContent).toContain('user_auth_unavailable');
    expect(screen.queryByRole('button', { name: 'Submit' })).toBeNull();
  });
  it.each([
    [422, 'invalid'],
    [409, 'capacity'],
    [409, 'stale'],
    [409, 'incomplete'],
    [403, 'forbidden'],
  ])('allows correction after proven rejection %s/%s', async (status, code) => {
    const original = globalThis.fetch;
    let attempts = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (...args: Parameters<typeof fetch>) => {
        if (String(args[0]).endsWith('/submit') && ++attempts === 1) {
          return { ok: false, status, json: async () => ({ detail: { code } }) } as Response;
        }
        return original(...args);
      }),
    );
    mount();
    await screen.findByRole('heading', { name: 'Card a' });
    fireEvent.click(screen.getAllByRole('radio', { name: 'Go' })[0]!);
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]!);
    await screen.findByRole('alert');
    await waitFor(() =>
      expect(
        (screen.getAllByLabelText('Reply (optional)')[0]! as HTMLTextAreaElement).disabled,
      ).toBe(false),
    );
    fireEvent.change(screen.getAllByLabelText('Reply (optional)')[0]!, {
      target: { value: 'corrected' },
    });
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]!);
    await screen.findByText('Read-only');
    expect(calls.find((c) => c.path.endsWith('/submit'))?.body.answer).toBe('corrected');
  });

  it('reuses exact submit identity after uncertain HTTP and never switches to reject', async () => {
    const original = globalThis.fetch;
    let attempts = 0;
    const payloads: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (...args: Parameters<typeof fetch>) => {
        if (String(args[0]).endsWith('/submit')) {
          payloads.push(String(args[1]?.body));
          if (++attempts === 1) throw new Error('connection lost');
        }
        return original(...args);
      }),
    );
    mount();
    await screen.findByRole('heading', { name: 'Card a' });
    fireEvent.click(screen.getAllByRole('radio', { name: 'Go' })[0]!);
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]!);
    await screen.findByRole('alert');
    fireEvent.click(screen.getAllByRole('button', { name: 'Reject' })[0]!);
    fireEvent.click(screen.getByRole('button', { name: 'Confirm rejection' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('uncertain'));
    fireEvent.click(screen.getAllByRole('button', { name: 'Submit' })[0]!);
    await screen.findByText('Read-only');
    expect(payloads).toHaveLength(2);
    expect(payloads[0]).toBe(payloads[1]);
  });
});
