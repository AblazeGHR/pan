import * as api from '@/services/api';
import { resolveRelatedMessageTarget } from '@/components/chat/relatedMessageTarget';
import { useRelatedMessageStore } from '@/stores/relatedMessageStore';
import { useSessionStore } from '@/stores/sessionStore';
vi.mock('@/components/chat/relatedMessageTarget', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/components/chat/relatedMessageTarget')>(), resolveRelatedMessageTarget: vi.fn(),
}));
import { useAutomationAuth } from '@/stores/automationStore';
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import LockerView from './LockerView';
import {
  lockerProjection,
  lockerTimeline,
  type UserCard,
  type InteractionCard,
  type LockerMessage,
  type Locker,
} from './lockerModel';
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
let userCards: UserCard[];
let cards: InteractionCard[];
let messages: LockerMessage[];
let calls: { path: string; body: Record<string, unknown>; method?: string }[];
beforeEach(() => {
  vi.mocked(resolveRelatedMessageTarget).mockReset();
  useAutomationAuth.setState({ token: 'unit-only', expiresAt: Date.now() + 60000 });
  uiState.activeWorkspaceId = 'all';
  userCards = [];
  cards = [card(), card('b', 'l2')];
  messages = [];
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
      if (path === '/api/user-cards') return { ok: true, json: async () => ({ cards: userCards }) };
      if (path.startsWith('/api/user-cards/')) {
        const id = path.split('/')[3];
        if (init?.method === 'DELETE') {
          userCards = userCards.filter((item) => item.id !== id);
          return { ok: true, json: async () => ({ deleted: true }) };
        }
        const updated = {...userCards.find((item) => item.id === id)!, read: body.read as boolean, revision: 2};
        userCards = userCards.map((item) => item.id === id ? updated : item);
        return { ok: true, json: async () => ({card: updated}) };
      }
      if (path.startsWith('/api/user-locker-messages?'))
        return { ok: true, json: async () => ({ messages, nextAfter: null }) };
      if (path.startsWith('/api/user-locker-messages/')) {
        const id = path.split('/')[3];
        if (init?.method === 'DELETE') {
          messages = messages.filter((item) => item.id !== id);
          return { ok: true, json: async () => ({ deleted: true }) };
        }
        const previous = messages.find((item) => item.id === id)!;
        const message = {
          ...previous,
          read: body.read as boolean,
          revision: previous.revision + 1,
        };
        messages = messages.map((item) => (item.id === id ? message : item));
        return { ok: true, json: async () => ({ message }) };
      }
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
  it('retains no-answer-needed cards visibly with only deletion and skips bulk read', async () => {
    cards[0] = { ...card(), status: 'no_answer_needed', revision: 1 };
    mount();
    const heading = await screen.findByRole('heading', { name: 'Card a' });
    const row = within(heading.closest('article')!);
    expect(row.getAllByText('无需回答 · No answer needed')).toHaveLength(2);
    expect(row.queryByRole('textbox')).toBeNull();
    expect(row.queryByRole('radio')).toBeNull();
    expect(row.queryByRole('button', { name: 'Submit' })).toBeNull();
    expect(row.queryByRole('button', { name: 'Reject' })).toBeNull();
    expect(row.queryByRole('button', { name: /Mark card/ })).toBeNull();
    fireEvent.click(
      within(screen.getByTestId('locker-source-l1')).getByRole('button', { name: 'Read all' }),
    );
    expect(calls.filter((c) => c.path === '/api/user-inbox/a/read')).toHaveLength(0);
    fireEvent.click(row.getByRole('button', { name: 'Delete record' }));
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Card a' })).toBeNull());
  });

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
    expect(screen.getByRole('textbox', { name: 'Reply (optional)' })).toHaveProperty(
      'value',
      'local draft',
    );
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
  it('mixes original Locker msg and Card records by timestamp and identity and clears ordinary records', async () => {
    messages = [
      {
        kind: 'message',
        revision: 0,
        id: 'm',
        lockerId: 'l1',
        ownerSessionId: 'ses_one',
        recipientId: 'user',
        read: false,
        createdAt: '2026-10-23T01:30:00Z',
        message: {
          messageId: 'source-msg',
          role: 'assistant',
          content: 'Ordinary body',
          ts: '2026-10-23T01:30:00Z',
        },
      },
    ];
    mount();
    await screen.findByText('Ordinary body', { selector: 'article div' });
    expect(
      [...screen.getByRole('region', { name: 'Locker messages' }).querySelectorAll('article')].map(
        (row) => row.getAttribute('data-card-id') ?? row.getAttribute('data-locker-message-id'),
      ),
    ).toEqual(['a', 'm', 'b']);
    const row = screen.getByText('Ordinary body', { selector: 'article div' }).closest('article')!;
    expect(within(row).queryByRole('button', { name: 'Submit' })).toBeNull();
    fireEvent.click(within(row).getByRole('button', { name: 'Mark read' }));
    await waitFor(() =>
      expect(within(row).getByRole('button', { name: 'Mark unread' })).toBeTruthy(),
    );
    fireEvent.click(within(row).getByRole('button', { name: 'Mark unread' }));
    await waitFor(() =>
      expect(within(row).getByRole('button', { name: 'Mark read' })).toBeTruthy(),
    );
    fireEvent.click(within(row).getByRole('button', { name: 'Delete record' }));
    await waitFor(() =>
      expect(screen.queryByText('Ordinary body', { selector: 'article div' })).toBeNull(),
    );
    expect(
      calls.find((call) => call.path === '/api/user-locker-messages/m' && call.method === 'DELETE'),
    ).toBeTruthy();
  });
  it('does not let a late read response overwrite a newer ordinary-message revision', async () => {
    const original = globalThis.fetch;
    messages = [
      {
        kind: 'message',
        revision: 0,
        id: 'late',
        lockerId: 'l1',
        ownerSessionId: 'ses_one',
        recipientId: 'user',
        read: false,
        createdAt: '2026-10-23T01:30:00Z',
        message: { messageId: 'original', role: 'assistant', content: 'Late read probe' },
      },
    ];
    const older = { ...messages[0]!, read: true, revision: 1 };
    let release: (value: Response) => void = () => {};
    const response = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (...args: Parameters<typeof fetch>) =>
        String(args[0]).endsWith('/late/read') ? response : original(...args),
      ),
    );
    mount();
    await screen.findByText('Late read probe', { selector: 'article div' });
    const row = screen
      .getByText('Late read probe', { selector: 'article div' })
      .closest('article')!;
    fireEvent.click(within(row).getByRole('button', { name: 'Mark read' }));
    messages[0] = { ...messages[0]!, read: false, revision: 2 };
    fireEvent.click(screen.getByRole('button', { name: 'Refresh Locker' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Refresh Locker' })).toHaveProperty(
        'disabled',
        false,
      ),
    );
    await act(async () =>
      release({ ok: true, json: async () => ({ message: older }) } as Response),
    );
    expect(within(row).getByRole('button', { name: 'Mark read' })).toBeTruthy();
    expect(within(row).queryByRole('button', { name: 'Mark unread' })).toBeNull();
  });
  it('does not retain visible cards or source metadata after logout', async () => {
    mount();
    await screen.findByRole('heading', { name: 'Card a' });
    act(() => useAutomationAuth.getState().clear());
    expect(screen.queryByRole('heading', { name: 'Card a' })).toBeNull();
    expect(screen.queryByTestId('locker-source-l1')).toBeNull();
    expect(screen.queryByText('ses_one')).toBeNull();
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

it('never marks visible Cards or ordinary messages read on entry, scroll, refresh or source switches', async () => {
  const observed: Element[] = [];
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      constructor(private callback: IntersectionObserverCallback) {}
      observe(element: Element) {
        observed.push(element);
        this.callback(
          [{ isIntersecting: true, target: element } as IntersectionObserverEntry],
          this as unknown as IntersectionObserver,
        );
      }
      disconnect() {}
    },
  );
  messages = [
    {
      kind: 'message',
      id: 'manual-only',
      lockerId: 'l1',
      ownerSessionId: 'ses_one',
      recipientId: 'user',
      read: false,
      revision: 0,
      createdAt: '2026-10-23T01:30:00Z',
      message: { messageId: 'manual-only', role: 'assistant', content: 'Manual reading only' },
    },
  ];
  mount();
  await screen.findByText('Manual reading only', { selector: 'article div' });
  fireEvent.scroll(screen.getByRole('region', { name: 'Locker messages' }));
  fireEvent.click(screen.getByTestId('locker-source-l1'));
  fireEvent.click(screen.getByText('All Lockers'));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh Locker' }));
  await waitFor(() =>
    expect(
      (screen.getByRole('button', { name: 'Refresh Locker' }) as HTMLButtonElement).disabled,
    ).toBe(false),
  );
  expect(observed).toEqual([]);
  expect(calls.filter((call) => call.path.endsWith('/read'))).toEqual([]);
  fireEvent.click(screen.getByRole('button', { name: /^Mark read$/ }));
  await screen.findByRole('button', { name: /^Mark unread$/ });
  expect(calls.filter((call) => call.path.endsWith('/read'))).toHaveLength(1);
});


it('shows context controls on cards and ordinary messages and transfers exact owner/id', async () => {
  cards = [{ ...card(), relatedMessageId: 'msg_card' }];
  messages = [{ kind: 'message', revision: 0, id: 'm', lockerId: 'l1', ownerSessionId: 'ses_one', recipientId: 'user',
    createdAt: '2026-10-25T00:00:00Z', read: false, relatedMessageId: 'msg_message',
    message: { role: 'assistant', content: 'Ordinary body' } }];
  vi.mocked(resolveRelatedMessageTarget).mockImplementation(async (_owner, id) => ({ offset: 3, total: 10, messageId: id }));
  const select = vi.spyOn(useSessionStore.getState(), 'selectSession').mockImplementation(async (id) => { useSessionStore.setState({ currentSessionId: id }); });
  mount();
  await waitFor(() => expect(screen.getAllByRole('button', { name: '跳转到相关上下文' })).toHaveLength(2));
  for (const [index, id] of ['msg_card', 'msg_message'].entries()) {
    const button = screen.getAllByRole('button', { name: '跳转到相关上下文' })[index]!;
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(useRelatedMessageStore.getState().target).toEqual({ sessionId: 'ses_one', messageId: id, offset: 3, total: 10 }));
    expect(select).toHaveBeenCalledWith('ses_one', expect.any(AbortSignal));
    expect(resolveRelatedMessageTarget).toHaveBeenCalledWith('ses_one', id, expect.any(AbortSignal));
  }
  select.mockRestore();
});

it('hides missing ids and leaves failed context resolution retryable with an explicit reason', async () => {
  cards = [{ ...card(), relatedMessageId: 'stale' }, card('b', 'l2')];
  vi.mocked(resolveRelatedMessageTarget).mockRejectedValue(new Error('相关消息已被替换'));
  mount();
  const button = await screen.findByRole('button', { name: '跳转到相关上下文' });
  expect((button as HTMLButtonElement).disabled).toBe(false);
  expect(resolveRelatedMessageTarget).not.toHaveBeenCalled();
  fireEvent.click(button);
  await waitFor(() => expect(screen.getByText('Error: 相关消息已被替换')).toBeTruthy());
  expect(screen.getAllByRole('button', { name: '跳转到相关上下文' })).toHaveLength(1);
  expect((button as HTMLButtonElement).disabled).toBe(false);
  expect(resolveRelatedMessageTarget).toHaveBeenCalledTimes(1);
  fireEvent.click(button);
  await waitFor(() => expect(resolveRelatedMessageTarget).toHaveBeenCalledTimes(2));
  await screen.findByText('Error: 相关消息已被替换');
});


it('does not show context buttons for either item type without a related id', async () => {
  cards = [card()];
  messages = [{ kind: 'message', revision: 0, id: 'm', lockerId: 'l1', ownerSessionId: 'ses_one', recipientId: 'user',
    createdAt: '2026-10-25T00:00:00Z', read: false, relatedMessageId: null,
    message: { role: 'assistant', content: 'No related id' } }];
  mount();
  await within(screen.getByRole('region', { name: 'Locker messages' })).findByText('No related id');
  expect(screen.queryByRole('button', { name: '跳转到相关上下文' })).toBeNull();
});

it('disables the context button when its owner Session is deleted', async () => {
  cards = [{ ...card('b', 'l2'), relatedMessageId: 'msg_deleted_owner' }];
  mount();
  await screen.findByText('所属 Session 已删除或不可用。');
  expect((screen.getByRole('button', { name: '跳转到相关上下文' }) as HTMLButtonElement).disabled).toBe(true);
});


it('renders twenty related entries with zero navigation requests on mount, hover, refresh or re-render', async () => {
  const actual = await vi.importActual<typeof import('@/components/chat/relatedMessageTarget')>('@/components/chat/relatedMessageTarget');
  vi.mocked(resolveRelatedMessageTarget).mockImplementation(actual.resolveRelatedMessageTarget);
  const navigation = vi.spyOn(api, 'fetchSessionNavigation').mockResolvedValue({ history: [], start: 0, total: 0, hasMore: false });
  cards = Array.from({ length: 10 }, (_, i) => ({ ...card(`card-${i}`), relatedMessageId: `msg_card_${i}` }));
  messages = Array.from({ length: 10 }, (_, i) => ({ kind: 'message', revision: 0, id: `message-${i}`, lockerId: 'l1',
    ownerSessionId: 'ses_one', recipientId: 'user', createdAt: '2026-10-25T00:00:00Z', read: false,
    relatedMessageId: `msg_message_${i}`, message: { role: 'assistant', content: `Message ${i}` } }));
  const view = mount();
  await waitFor(() => expect(screen.getAllByRole('button', { name: '跳转到相关上下文' })).toHaveLength(20));
  expect(navigation).not.toHaveBeenCalled();
  expect(resolveRelatedMessageTarget).not.toHaveBeenCalled();
  fireEvent.mouseEnter(screen.getAllByRole('button', { name: '跳转到相关上下文' })[0]!);
  view.rerender(<MemoryRouter><LockerView /></MemoryRouter>);
  fireEvent.click(screen.getByRole('button', { name: 'Refresh Locker' }));
  await waitFor(() => expect((screen.getByRole('button', { name: 'Refresh Locker' }) as HTMLButtonElement).disabled).toBe(false));
  expect(screen.getAllByRole('button', { name: '跳转到相关上下文' })).toHaveLength(20);
  expect(navigation).not.toHaveBeenCalled();
  expect(resolveRelatedMessageTarget).not.toHaveBeenCalled();
  expect(calls.filter(({ path }) => path.includes('/navigation'))).toEqual([]);
  navigation.mockRestore();
});

function reverseCard(answered = true): UserCard {
  return {id: 'uc', direction: 'user', kind: 'question', ownerSessionId: 'ses_one', lockerId: 'l1',
    body: '**User question body**', attachments: [{id: 'att_one', name: 'evidence.txt'}],
    createdAt: '2026-10-23T01:00:00Z', answeredAt: answered ? '2026-10-24T03:00:00Z' : null,
    status: answered ? 'answered' : 'pending', answer: answered ? 'Agent answer body' : null,
    read: !answered, revision: answered ? 1 : 0};
}
describe('user card positions share one record', () => {
  it('renders pending once without read controls and keeps answered projections ordered deterministically', async () => {
    cards = []; userCards = [reverseCard(false)]; mount();
    await screen.findByText('等待回答 · Awaiting answer');
    expect(document.querySelectorAll('[data-user-card-id="uc"]')).toHaveLength(1);
    expect(screen.queryByRole('button', {name: /Mark user card/})).toBeNull();
    const sameTime = {...reverseCard(), answeredAt: reverseCard().createdAt};
    expect(lockerTimeline([sameTime]).map((entry) => entry.position)).toEqual(['created', 'answered']);
    expect(new Set(lockerTimeline([sameTime]).map((entry) => entry.key)).size).toBe(2);
    const positions = lockerTimeline([reverseCard(), card('b')]);
    expect(positions.map((entry) => entry.item.id)).toEqual(['uc', 'b', 'uc']);
  });
  it.each([0, 1])('read and delete from position %i update/remove both identical rows', async (position) => {
    cards = []; userCards = [reverseCard()]; mount();
    await waitFor(() => expect(document.querySelectorAll('[data-user-card-id="uc"]')).toHaveLength(2));
    let rows = [...document.querySelectorAll<HTMLElement>('[data-user-card-id="uc"]')];
    expect(rows[0]!.textContent).toBe(rows[1]!.textContent);
    expect(screen.getAllByText('Agent answer body')).toHaveLength(2);
    expect(rows.every((row) => row.querySelector('textarea,input') === null)).toBe(true);
    const reads = screen.getAllByRole('button', {name: 'Mark user card as read'});
    fireEvent.click(reads[position]!);
    await waitFor(() => expect(screen.getAllByRole('button', {name: 'Mark user card as unread'})).toHaveLength(2));
    rows = [...document.querySelectorAll<HTMLElement>('[data-user-card-id="uc"]')];
    expect(rows[0]!.textContent).toBe(rows[1]!.textContent);
    expect(calls.filter((call) => call.path === '/api/user-cards/uc/read')).toHaveLength(1);
    expect(userCards).toHaveLength(1);
    expect(calls.filter((call) => /attachments/.test(call.path))).toHaveLength(0);
    fireEvent.click(screen.getAllByRole('button', {name: 'Delete user card'})[position]!);
    await waitFor(() => expect(document.querySelectorAll('[data-user-card-id="uc"]')).toHaveLength(0));
    expect(userCards).toHaveLength(0);
  });
});
