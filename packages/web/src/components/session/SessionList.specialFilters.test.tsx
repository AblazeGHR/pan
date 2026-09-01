// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, cleanup, act } from '@testing-library/react';
import { SessionList } from './SessionList';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { hasSubagents, isMetaAgent } from '@/utils/sessionFilters';
import type { Session } from '@/types';

function mk(id: string, name: string, extra: Partial<Session> = {}): Session {
  return { id, name, alwaysThinkingEnabled: false, effort: '', history: [], ...extra };
}

const HIDDEN_KEY = 'pan:hiddenSessions';

function persistedHidden(): Set<string> {
  try {
    return new Set<string>(JSON.parse(localStorage.getItem(HIDDEN_KEY) ?? '[]'));
  } catch {
    return new Set<string>();
  }
}

function resetStores() {
  localStorage.clear();
  useSessionStore.setState({
    sessions: [],
    currentSessionId: null,
    multiSelectMode: false,
    selectedIds: new Set(),
  });
  useUIStore.setState({
    groupBy: 'none',
    searchQuery: '',
    sortBy: 'recent',
    specialFilters: new Set(),
    hiddenSessionIds: new Set(),
    collapsedGroups: new Set(),
  });
}

describe('special session filters (has subagent / is MetaAgent)', () => {
  beforeEach(() => {
    resetStores();
  });

  it('unit: hasSubagents via managed array and via managedBy back-reference', () => {
    const parent = mk('p', 'Parent', { managed: ['c1'] });
    expect(hasSubagents(parent, [parent, mk('c1', 'Child')])).toBe(true);

    // Fallback: no managed array, but a child session points back via managedBy.
    const parentNoManaged = mk('p2', 'Parent');
    const child = mk('c2', 'Child', { managedBy: 'p2' });
    expect(hasSubagents(parentNoManaged, [parentNoManaged, child])).toBe(true);
    expect(hasSubagents(child, [parentNoManaged, child])).toBe(false);
    expect(hasSubagents(mk('plain', 'Plain'), [mk('plain', 'Plain')])).toBe(false);
  });

  it('unit: isMetaAgent requires pan MCP enabled and effective', () => {
    expect(isMetaAgent(mk('a', 'A', { mcpServers: ['pan'] }))).toBe(true);
    expect(isMetaAgent(mk('a', 'A', { mcpServers: ['pan', 'git'] }))).toBe(true);
    expect(isMetaAgent(mk('b', 'B', { mcpServers: ['git'] }))).toBe(false);
    expect(isMetaAgent(mk('c', 'C'))).toBe(false);
    // Template locked to mcp_mode "never" disables MCP even if the list is stale.
    expect(isMetaAgent(mk('d', 'D', { mcpServers: ['pan'], mcpLockReason: 'never' }))).toBe(false);
  });

  it('filters by "has subagent"', () => {
    useSessionStore.setState({
      sessions: [
        mk('parent', 'Parent', { managed: ['child'] }),
        mk('child', 'Child', { managedBy: 'parent' }),
        mk('plain', 'Plain'),
      ],
    });
    useUIStore.setState({ specialFilters: new Set(['subagent']) });

    const { container } = render(<SessionList />);
    expect(container.textContent).toContain('Parent');
    expect(container.textContent).not.toContain('Child');
    expect(container.textContent).not.toContain('Plain');
  });

  it('filters by "is MetaAgent" and excludes locked-never sessions', () => {
    useSessionStore.setState({
      sessions: [
        mk('meta', 'Meta', { mcpServers: ['pan'] }),
        mk('locked', 'Locked', { mcpServers: ['pan'], mcpLockReason: 'never' }),
        mk('noMcp', 'NoMcp', { mcpServers: ['git'] }),
      ],
    });
    useUIStore.setState({ specialFilters: new Set(['metaagent']) });

    const { container } = render(<SessionList />);
    expect(container.textContent).toContain('Meta');
    expect(container.textContent).not.toContain('Locked');
    expect(container.textContent).not.toContain('NoMcp');
  });

  it('combines special filters (AND) and clears them', () => {
    useSessionStore.setState({
      sessions: [
        mk('both', 'Both', { managed: ['c'], mcpServers: ['pan'] }),
        mk('subOnly', 'SubOnly', { managed: ['c'] }),
        mk('metaOnly', 'MetaOnly', { mcpServers: ['pan'] }),
      ],
    });

    useUIStore.setState({ specialFilters: new Set(['subagent', 'metaagent']) });
    const { container, rerender } = render(<SessionList />);
    expect(container.textContent).toContain('Both');
    expect(container.textContent).not.toContain('SubOnly');
    expect(container.textContent).not.toContain('MetaOnly');

    // Clear both filters → everything is back.
    act(() => {
      useUIStore.setState({ specialFilters: new Set() });
    });
    rerender(<SessionList />);
    expect(container.textContent).toContain('SubOnly');
    expect(container.textContent).toContain('MetaOnly');
  });

  it('composes with the text search without breaking it', () => {
    useSessionStore.setState({
      sessions: [
        mk('metaA', 'Alpha', { mcpServers: ['pan'] }),
        mk('metaB', 'Beta', { mcpServers: ['pan'] }),
        mk('plain', 'Plain'),
      ],
    });
    useUIStore.setState({ specialFilters: new Set(['metaagent']), searchQuery: 'beta' });

    const { container } = render(<SessionList />);
    expect(container.textContent).toContain('Beta');
    expect(container.textContent).not.toContain('Alpha');
    expect(container.textContent).not.toContain('Plain');
  });

  it('shows the empty state when a special filter matches nothing', () => {
    useSessionStore.setState({ sessions: [mk('plain', 'Plain')] });
    useUIStore.setState({ specialFilters: new Set(['metaagent']) });

    const { container } = render(<SessionList />);
    expect(container.textContent).toContain('No matching sessions');
  });
});

describe('select-mode hide/show sessions', () => {
  beforeEach(() => {
    resetStores();
  });

  afterEach(() => {
    cleanup();
  });

  it('hides sessions in normal mode and shows them in select mode', () => {
    useSessionStore.setState({ sessions: [mk('a', 'Alpha'), mk('b', 'Beta')] });
    useUIStore.setState({ hiddenSessionIds: new Set(['a']) });

    // Normal mode: hidden Alpha is excluded.
    const { container } = render(<SessionList />);
    expect(container.textContent).toContain('Beta');
    expect(container.textContent).not.toContain('Alpha');

    // Select mode: both are shown; Alpha carries the "Show session" eye button,
    // Beta the "Hide session" one.
    act(() => {
      useSessionStore.setState({ multiSelectMode: true, selectedIds: new Set() });
    });
    expect(container.textContent).toContain('Alpha');
    expect(container.querySelector('button[title="Show session"]')).not.toBeNull();
    expect(container.querySelector('button[title="Hide session"]')).not.toBeNull();
  });

  it('shows the "All sessions hidden" empty state when every session is hidden', () => {
    useSessionStore.setState({ sessions: [mk('a', 'Alpha')] });
    useUIStore.setState({ hiddenSessionIds: new Set(['a']) });

    const { container } = render(<SessionList />);
    expect(container.textContent).toContain('All sessions hidden');
  });

  it('eye button hides a session and persists across refresh/rerender (by id)', () => {
    useSessionStore.setState({ sessions: [mk('a', 'Alpha'), mk('b', 'Beta')] });
    useUIStore.setState({ hiddenSessionIds: new Set() });

    const { container, unmount } = render(<SessionList />);

    // Enter select mode, close the eyes of the visible Beta (second card).
    act(() => {
      useSessionStore.setState({ multiSelectMode: true, selectedIds: new Set() });
    });
    const hideButtons = container.querySelectorAll('button[title="Hide session"]');
    fireEvent.click(hideButtons[1]!);
    expect(useUIStore.getState().hiddenSessionIds.has('b')).toBe(true);
    expect(persistedHidden().has('b')).toBe(true);
    expect(useUIStore.getState().hiddenSessionIds.has('a')).toBe(false);

    // List refresh (new array, same ids): hidden state survives.
    act(() => {
      useSessionStore.setState({
        sessions: [mk('a', 'Alpha'), mk('b', 'Beta')],
        multiSelectMode: false,
        selectedIds: new Set(),
      });
    });
    expect(container.textContent).toContain('Alpha');
    expect(container.textContent).not.toContain('Beta');

    // Reload simulation: fresh uiStore hydrated from localStorage.
    act(() => {
      useUIStore.setState({ hiddenSessionIds: persistedHidden() });
    });
    expect(container.textContent).toContain('Alpha');
    expect(container.textContent).not.toContain('Beta');

    // Fresh mount in normal mode: Beta is still hidden, Alpha visible.
    unmount();
    const remount = render(<SessionList />);
    expect(remount.container.textContent).toContain('Alpha');
    expect(remount.container.textContent).not.toContain('Beta');
  });

  it('eye button shows a hidden session again', () => {
    useSessionStore.setState({ sessions: [mk('a', 'Alpha'), mk('b', 'Beta')] });
    useUIStore.setState({ hiddenSessionIds: new Set(['a']) });

    const { container } = render(<SessionList />);
    expect(container.textContent).not.toContain('Alpha');

    act(() => {
      useSessionStore.setState({ multiSelectMode: true, selectedIds: new Set() });
    });
    fireEvent.click(container.querySelector('button[title="Show session"]')!);
    expect(useUIStore.getState().hiddenSessionIds.has('a')).toBe(false);
    expect(persistedHidden().has('a')).toBe(false);

    // Exit select mode: Alpha is visible again.
    act(() => {
      useSessionStore.setState({ multiSelectMode: false, selectedIds: new Set() });
    });
    expect(container.textContent).toContain('Alpha');
  });

  it('eye button does not toggle the card selection', () => {
    useSessionStore.setState({ sessions: [mk('a', 'Alpha'), mk('b', 'Beta')] });
    useUIStore.setState({ hiddenSessionIds: new Set() });

    const { container } = render(<SessionList />);
    act(() => {
      useSessionStore.setState({ multiSelectMode: true, selectedIds: new Set() });
    });

    // Clicking the eye must not select the card.
    fireEvent.click(container.querySelector('button[title="Hide session"]')!);
    expect(useSessionStore.getState().selectedIds.size).toBe(0);

    // Clicking the card row still selects (and does not touch the hidden set).
    const rows = container.querySelectorAll('.flex.items-center.gap-2.px-3.py-2');
    fireEvent.click(rows[0] as HTMLElement);
    expect(useSessionStore.getState().selectedIds.size).toBe(1);
    expect(useUIStore.getState().hiddenSessionIds.size).toBe(1);
  });

  it('select mode still applies special filters on top of showing hidden ones', () => {
    useSessionStore.setState({
      sessions: [mk('a', 'Alpha', { mcpServers: ['pan'] }), mk('b', 'Beta')],
    });
    useUIStore.setState({ hiddenSessionIds: new Set(['a']) });
    useUIStore.setState({ specialFilters: new Set(['metaagent']) });

    const { container } = render(<SessionList />);
    // Normal mode: hidden Alpha is excluded → nothing matches → empty state.
    expect(container.textContent).toContain('No matching sessions');

    // Select mode: hidden Alpha matches the MetaAgent filter and appears.
    act(() => {
      useSessionStore.setState({ multiSelectMode: true, selectedIds: new Set() });
    });
    expect(container.textContent).toContain('Alpha');
    expect(container.textContent).not.toContain('Beta');
  });

  it('manager tree: hidden children excluded in normal mode, shown in select mode', () => {
    useSessionStore.setState({
      sessions: [mk('parent', 'Parent'), mk('child', 'Child', { managedBy: 'parent' })],
    });
    useUIStore.setState({ groupBy: 'manager', hiddenSessionIds: new Set(['child']) });

    const { container } = render(<SessionList />);
    expect(container.textContent).toContain('Parent');
    expect(container.textContent).not.toContain('Child');

    act(() => {
      useSessionStore.setState({ multiSelectMode: true, selectedIds: new Set() });
    });
    expect(container.textContent).toContain('Child');
  });

  it('prunes hidden ids of deleted sessions', () => {
    useSessionStore.setState({ sessions: [mk('a', 'Alpha')] });
    useUIStore.setState({ hiddenSessionIds: new Set(['a', 'ghost']) });

    render(<SessionList />);
    const hidden = useUIStore.getState().hiddenSessionIds;
    expect(hidden.has('a')).toBe(true);
    expect(hidden.has('ghost')).toBe(false);
  });
});
