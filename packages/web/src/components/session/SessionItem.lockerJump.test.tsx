// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SessionItem } from './SessionItem';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useJobStore } from '@/stores/jobStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useAdapterStore } from '@/stores/adapterStore';
import type { Session } from '@/types';

const unread = vi.hoisted(() => new Map<string, number>());
vi.mock('@/stores/lockerUnreadStore', () => ({
  useLockerSessionUnread: (id: string) => unread.get(id) ?? 0,
}));

const session = (id = 'session-1'): Session => ({
  id,
  name: 'Codex',
  adapter: 'codex',
  alwaysThinkingEnabled: false,
  effort: '',
  history: [],
  historyTotal: 3,
  lastMessage: 'Hello there',
  model: 'gpt-5-codex',
  workerStatus: 'idle',
});

function mount(card = session()) {
  return render(
    <MemoryRouter>
      <SessionItem session={card} isActive={false} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  unread.clear();
  useSessionStore.setState({ sessions: [] });
  useUIStore.setState({ activeWorkspaceId: 'all' });
  useJobStore.setState({ jobs: [], loaded: true });
  useWorkspaceStore.setState({ workspaces: [], loaded: true });
  useAdapterStore.setState({ adapterConfigs: {}, currentAdapter: 'codex' });
});
afterEach(cleanup);

describe('Session row Locker jump', () => {
  it('renders nothing at all when this Session has no unread Locker items', () => {
    mount();
    expect(screen.queryByTestId('session-locker-jump')).toBeNull();
  });

  it('shows the count left of the adapter badge, inside the fixed info row', () => {
    unread.set('session-1', 2);
    mount();
    const button = screen.getByTestId('session-locker-jump');
    const adapter = screen.getByTestId('session-adapter-badge');
    expect(button.parentElement?.className).toContain('session-quick-action-adapter-row');
    expect(button.nextElementSibling).toBe(adapter);
    expect(button.textContent).toContain('2');
    expect(button.getAttribute('aria-label')).toContain('未读 2 条');
    expect(button.getAttribute('aria-label')).toContain('Codex');
  });

  it('keeps the title and model lines outside the jump button', () => {
    unread.set('session-1', 1);
    mount();
    const button = screen.getByTestId('session-locker-jump');
    const title = screen.getByText('Codex');
    const model = screen.getByText(/gpt-5-codex/);
    expect(title.contains(button)).toBe(false);
    expect(model.contains(button)).toBe(false);
  });

  it('updates per Session: another row stays hidden', () => {
    unread.set('session-1', 4);
    mount();
    expect(screen.getByTestId('session-locker-jump')).toBeTruthy();
    cleanup();
    mount(session('session-2'));
    expect(screen.queryByTestId('session-locker-jump')).toBeNull();
  });
});
