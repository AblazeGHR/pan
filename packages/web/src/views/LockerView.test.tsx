// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import LockerView from './LockerView';
import { lockerProjection, type InteractionCard, type Locker } from './lockerModel';
vi.mock('@/components/chat/MarkdownRenderer', () => ({ MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div> }));
vi.mock('@/stores/workspaceStore', () => {
  const useWorkspaceStore = Object.assign((select: (s: unknown) => unknown) => select({ workspaces: [{ id: 'w', name: 'Work' }] }),
    { getState: () => ({ loadWorkspaces: vi.fn() }) });
  return { useWorkspaceStore };
});
const lockers: Locker[] = [
  { id: 'l1', sessionId: 'ses_one', sourceName: 'Source One', workspaceIds: ['w'], available: true, total: 1, unread: 1 },
  { id: 'l2', sessionId: 'ses_two', sourceName: 'Source Two', workspaceIds: [], available: false, total: 1, unread: 1 },
];
function card(id = 'a', lockerId = 'l1'): InteractionCard {
  return { id, lockerId, ownerSessionId: lockerId === 'l1' ? 'ses_one' : 'ses_two', createdAt: id === 'a' ? '2026-10-23T01:00:00Z' : '2026-10-23T02:00:00Z',
    read: false, revision: 0, status: 'open', definition: { title: `Card ${id}`, body: '**Body**', summaryBody: 'Summary', answerInput: { label: 'Reply' },
      choices: [{ id: 'go', label: 'Go', triggerRef: { id: 'existing-trigger' } }] }, response: null, delivery: { state: 'not_requested' } };
}
function first<T>(values: T[]): T { const value = values[0]; if (value === undefined) throw new Error('Expected nonempty test result'); return value; }
let cards: InteractionCard[];
let calls: { path: string; body: Record<string, unknown>; method?: string }[];
beforeEach(() => {
  cards = [card(), card('b', 'l2')]; calls = [];
  vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? '{}')); calls.push({ path, body, method: init?.method });
    if (path === '/api/user-lockers') return { ok: true, json: async () => ({ lockers }) };
    if (path.startsWith('/api/user-inbox?')) return { ok: true, json: async () => ({ cards, nextAfter: null }) };
    const id = path.split('/')[3]; const index = cards.findIndex(c => c.id === id); const previous = cards[index];
    if (!previous) throw new Error(`Unknown test Card ${id}`);
    if (init?.method === 'DELETE') { cards = cards.filter(c => c.id !== id); return { ok: true, json: async () => ({ deleted: true }) }; }
    if (path.endsWith('/submit') || path.endsWith('/reject')) cards[index] = { ...previous, revision: 1,
      status: path.endsWith('/reject') ? 'rejected' : 'responded', response: { answer: body.answer, choiceId: body.choiceId } };
    if (path.endsWith('/read')) cards[index] = { ...previous, read: body.read };
    return { ok: true, json: async () => ({ card: cards[index] }) };
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function mount() { return render(<MemoryRouter><LockerView /></MemoryRouter>); }
describe('Locker panel', () => {
  it('aggregates by Workspace with original identity and time order', () => {
    expect(lockerProjection(lockers, [card('b', 'l2'), card()], '', 'aggregate').map(c => c.id)).toEqual(['a', 'b']);
    expect(lockerProjection(lockers, cards, 'w', 'aggregate').map(c => c.id)).toEqual(['a']);
    expect(lockerProjection(lockers, cards, '', 'l2').map(c => c.id)).toEqual(['b']);
  });
  it('shows independent Locker sources and Workspace projection', async () => {
    mount(); await screen.findByText('Card b');
    expect(screen.getByRole('main', { name: 'Locker' })).toBeTruthy();
    expect(screen.getAllByText('ses_one').length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText('Workspace'), { target: { value: 'w' } });
    expect(screen.queryByText('Card b')).toBeNull();
    expect(screen.getByText('Card a')).toBeTruthy();
    expect(calls.filter(c => c.method !== 'GET')).toHaveLength(0);
  });
  it('submits answer and choice once, becomes readonly, then deletes', async () => {
    mount(); await screen.findByText('Card a');
    fireEvent.change(first(screen.getAllByLabelText('Reply')), { target: { value: 'Approved' } });
    fireEvent.change(first(screen.getAllByLabelText('Choice')), { target: { value: 'go' } });
    fireEvent.click(first(screen.getAllByText('Submit')));
    await screen.findByText('已提交');
    expect((first(screen.getAllByLabelText('Reply')) as HTMLTextAreaElement).disabled).toBe(true);
    expect(calls.find(c => c.path.endsWith('/submit'))?.body).toMatchObject({ answer: 'Approved', choiceId: 'go', expectedRevision: 0 });
    fireEvent.click(screen.getByText('Delete'));
    await waitFor(() => expect(screen.queryByText('Card a')).toBeNull());
    expect(calls.filter(c => c.path.endsWith('/reject'))).toHaveLength(0);
  });
  it('rejects without answer, preserves card, allows delete and unread toggle', async () => {
    mount(); await screen.findByText('Card a');
    fireEvent.click(first(screen.getAllByText('Reject'))); await screen.findByText('已拒收');
    expect(screen.getByText('Card a')).toBeTruthy();
    expect(calls.find(c => c.path.endsWith('/reject'))?.body).not.toHaveProperty('answer');
    fireEvent.click(first(screen.getAllByText('标为已读')));
    await waitFor(() => expect(calls.some(c => c.path.endsWith('/a/read') && c.body.read === true)).toBe(true));
  });
  it('pages visible messages without another container and reads server response after refresh', async () => {
    cards = Array.from({ length: 21 }, (_, i) => card(`page-${String(i).padStart(2, '0')}`));
    mount(); await screen.findByText('Card page-00');
    expect(screen.queryByText('Card page-20')).toBeNull();
    fireEvent.click(screen.getByText('加载更多'));
    expect(screen.getByText('Card page-20')).toBeTruthy();
    fireEvent.change(first(screen.getAllByLabelText('Reply')), { target: { value: 'local draft' } });
    const previous = first(cards);
    cards[0] = { ...previous, status: 'responded', revision: 1, response: { answer: 'server answer' } };
    fireEvent.click(screen.getByText('刷新'));
    await waitFor(() => expect((first(screen.getAllByLabelText('Reply')) as HTMLTextAreaElement).value).toBe('server answer'));
    expect((first(screen.getAllByLabelText('Reply')) as HTMLTextAreaElement).disabled).toBe(true);
  });

  it('shows unavailable authentication instead of granting Agent user rights', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({ detail: { code: 'user_auth_unavailable' } }) })));
    mount(); expect((await screen.findByRole('alert')).textContent).toContain('user_auth_unavailable');
    expect(screen.queryByText('Submit')).toBeNull();
  });
  it('reuses exact submit identity after uncertain HTTP and never switches to reject', async () => {
    const original = globalThis.fetch; let attempts = 0; const payloads: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (...args: Parameters<typeof fetch>) => {
      if (String(args[0]).endsWith('/submit')) {
        payloads.push(String(args[1]?.body));
        if (++attempts === 1) throw new Error('connection lost');
      }
      return original(...args);
    }));
    mount(); await screen.findByText('Card a'); fireEvent.click(first(screen.getAllByText('Submit')));
    await screen.findByRole('alert');
    fireEvent.click(first(screen.getAllByText('Reject')));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('请先刷新'));
    fireEvent.click(first(screen.getAllByText('Submit'))); await screen.findByText('已提交');
    expect(payloads).toHaveLength(2); expect(payloads[0]).toBe(payloads[1]);
    await act(async () => {});
  });
});
