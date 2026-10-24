// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { SessionFilterMenu } from './SessionFilterMenu';
import { SessionList } from '@/components/session/SessionList';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

function mk(id: string, name: string, extra: Partial<Session> = {}): Session {
  return { id, name, alwaysThinkingEnabled: false, effort: '', history: [], ...extra };
}

const SESSIONS: Session[] = [
  mk('cbc-1', 'Cbc one', { adapter: 'cbc' }),
  mk('cbc-2', 'Cbc two', { adapter: 'cbc', workerStatus: 'running' }),
  mk('codex-1', 'Codex one', { adapter: 'codex' }),
  mk('kimi-1', 'Kimi one', { adapter: 'kimi' }),
  mk('none-1', 'No adapter'),
];

function resetStores() {
  localStorage.clear();
  useSessionStore.setState({
    sessions: SESSIONS,
    currentSessionId: null,
    multiSelectMode: false,
    selectedIds: new Set(),
  });
  useUIStore.setState({
    searchQuery: '',
    specialFilters: new Set(),
    adapterFilters: new Set(),
    hiddenSessionIds: new Set(),
    collapsedGroups: new Set(),
    activeWorkspaceId: 'all',
  });
}

function openMenu() {
  fireEvent.click(screen.getByTitle('Special filters'));
  return screen.getByTestId('session-filter-menu');
}

function groupHeader(menu: HTMLElement, group: 'adapter' | 'state') {
  return within(menu).getByTestId(`session-filter-group-${group}`);
}

function adapterCheckbox(menu: HTMLElement, adapter: string) {
  const item = within(menu)
    .getAllByRole('menuitemcheckbox')
    .find((node) => node.textContent === adapter);
  if (!item) throw new Error(`adapter checkbox ${adapter} not found`);
  return item.querySelector('input[type="checkbox"]') as HTMLInputElement;
}

function stateCheckbox(menu: HTMLElement, label: string) {
  const item = within(menu)
    .getAllByRole('menuitemcheckbox')
    .find((node) => node.textContent?.includes(label));
  if (!item) throw new Error(`state checkbox ${label} not found`);
  return item.querySelector('input[type="checkbox"]') as HTMLInputElement;
}

function visibleSessionNames(container: HTMLElement): string[] {
  return [...container.querySelectorAll('[data-session-card-id]')].map(
    (card) => card.getAttribute('data-session-card-id') ?? '',
  );
}

beforeEach(() => {
  resetStores();
});

afterEach(() => {
  cleanup();
});

describe('SessionFilterMenu structure and a11y', () => {
  it('exposes the trigger as a collapsed menu button', () => {
    render(<SessionFilterMenu />);
    const trigger = screen.getByTitle('Special filters');
    expect(trigger.getAttribute('aria-haspopup')).toBe('menu');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('renders two collapsible second-level groups; State starts expanded, Adapter collapsed', () => {
    render(<SessionFilterMenu />);
    const menu = openMenu();
    const adapter = groupHeader(menu, 'adapter');
    const state = groupHeader(menu, 'state');
    expect(adapter.getAttribute('aria-expanded')).toBe('false');
    expect(state.getAttribute('aria-expanded')).toBe('true');
    expect(adapter.textContent).toContain('Adapter');
    expect(state.textContent).toContain('State');
    // The pre-existing State filters stay immediately available; no adapter
    // item shows until the Adapter group is expanded.
    expect(within(menu).getAllByRole('menuitemcheckbox')).toHaveLength(7);
    expect(within(menu).queryByText('cbc')).toBeNull();
  });

  it('group headers are native buttons and items are real checkboxes (keyboard operable)', () => {
    render(<SessionFilterMenu />);
    const menu = openMenu();
    const adapter = groupHeader(menu, 'adapter');
    expect(adapter.tagName).toBe('BUTTON');
    fireEvent.click(adapter);
    const checkbox = adapterCheckbox(menu, 'cbc');
    expect(checkbox.type).toBe('checkbox');
    expect(checkbox.getAttribute('role')).toBeNull(); // native checkbox role via input type
  });

  it('derives adapter options from the live session list (no hardcoded registry)', () => {
    render(<SessionFilterMenu />);
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    const options = [
      ...menu.querySelectorAll('#session-filter-adapter-items [role="menuitemcheckbox"]'),
    ].map((node) => node.textContent ?? '');
    expect(options).toEqual(['cbc', 'codex', 'kimi']);
  });

  it('shows a hint instead of options when no session carries an adapter', () => {
    useSessionStore.setState({ sessions: [mk('a', 'A')] });
    render(<SessionFilterMenu />);
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    expect(within(menu).getByText('No adapters in the session list')).toBeTruthy();
    expect(menu.querySelector('#session-filter-adapter-items [role="menuitemcheckbox"]')).toBeNull();
  });

  it('moves the existing State filters under the State group with their labels', () => {
    render(<SessionFilterMenu />);
    const menu = openMenu();
    const labels = within(menu)
      .getAllByRole('menuitemcheckbox')
      .map((node) => node.textContent ?? '');
    expect(labels).toEqual([
      'Has subagentSession manages at least one other session',
      'Is MetaAgentSession mounts the pan MCP server',
      'Running stateActual Worker runtime status is running',
      'Idle stateActual Worker runtime status is idle',
      'Illegal stateLegal state is running, but actual Worker status is not',
      'Has unreadUnread done count is greater than zero',
      'Has queued messagesPending queue count is greater than zero',
    ]);
  });
});

describe('SessionFilterMenu interaction contract', () => {
  it('checking an adapter keeps the menu open and updates the store', () => {
    render(<SessionFilterMenu />);
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(adapterCheckbox(menu, 'codex'));
    expect(screen.getByTestId('session-filter-menu')).toBeTruthy();
    expect([...useUIStore.getState().adapterFilters]).toEqual(['codex']);
    expect(screen.getByTestId('session-filter-count').textContent).toBe('1');
  });

  it('collapsing a group never clears its checkmarks', () => {
    render(<SessionFilterMenu />);
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(adapterCheckbox(menu, 'codex'));
    fireEvent.click(adapterCheckbox(menu, 'kimi'));

    // Collapse → items detach, selection survives in the store.
    fireEvent.click(groupHeader(menu, 'adapter'));
    expect(groupHeader(menu, 'adapter').getAttribute('aria-expanded')).toBe('false');
    expect([...useUIStore.getState().adapterFilters]).toEqual(['codex', 'kimi']);
    expect(screen.getByTestId('session-filter-count').textContent).toBe('2');

    // Re-expand → both checkmarks are still there.
    fireEvent.click(groupHeader(menu, 'adapter'));
    expect(adapterCheckbox(menu, 'codex').checked).toBe(true);
    expect(adapterCheckbox(menu, 'kimi').checked).toBe(true);
    expect(adapterCheckbox(menu, 'cbc').checked).toBe(false);
  });

  it('keeps both groups independent with a combined count', () => {
    render(<SessionFilterMenu />);
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(adapterCheckbox(menu, 'codex'));
    fireEvent.click(stateCheckbox(menu, 'Running state'));
    expect(screen.getByTestId('session-filter-count').textContent).toBe('2');
    expect([...useUIStore.getState().specialFilters]).toEqual(['running']);
  });

  it('Clear filters appears only while something is active and clears both groups', () => {
    render(<SessionFilterMenu />);
    const menu = openMenu();
    expect(within(menu).queryByRole('button', { name: 'Clear filters' })).toBeNull();

    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(adapterCheckbox(menu, 'codex'));
    fireEvent.click(stateCheckbox(menu, 'Running state'));

    fireEvent.click(within(menu).getByRole('button', { name: 'Clear filters' }));
    expect(useUIStore.getState().adapterFilters.size).toBe(0);
    expect(useUIStore.getState().specialFilters.size).toBe(0);
    expect(screen.queryByTestId('session-filter-count')).toBeNull();
    // Clearing does not close the menu.
    expect(screen.getByTestId('session-filter-menu')).toBeTruthy();
  });

  it('Escape closes the menu and returns focus to the trigger', () => {
    render(<SessionFilterMenu />);
    openMenu();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(screen.getByTitle('Special filters'));
  });

  it('an outside click on the backdrop closes the menu', () => {
    render(<SessionFilterMenu />);
    openMenu();
    fireEvent.click(document.querySelector('div.fixed.inset-0.z-20')!);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('the trigger toggles the menu open and closed', () => {
    render(<SessionFilterMenu />);
    const trigger = screen.getByTitle('Special filters');
    fireEvent.click(trigger);
    expect(screen.getByRole('menu')).toBeTruthy();
    fireEvent.click(trigger);
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('menu interactions never touch the persisted session-group collapse state', () => {
    localStorage.setItem('pan:collapsedGroups', JSON.stringify(['session-1']));
    render(<SessionFilterMenu />);
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(adapterCheckbox(menu, 'codex'));
    fireEvent.click(stateCheckbox(menu, 'Running state'));
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(localStorage.getItem('pan:collapsedGroups')).toBe(JSON.stringify(['session-1']));
  });
});

describe('SessionFilterMenu drives the real session list', () => {
  function renderMenuAndList() {
    return render(
      <>
        <SessionFilterMenu />
        <SessionList />
      </>,
    );
  }

  it('no adapter selected filters nothing (default)', () => {
    const { container } = renderMenuAndList();
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(groupHeader(menu, 'adapter')); // expand + collapse, nothing checked
    expect(visibleSessionNames(container)).toEqual(['cbc-1', 'cbc-2', 'codex-1', 'kimi-1', 'none-1']);
  });

  it('a selected adapter narrows the list; unchecking restores it', () => {
    const { container } = renderMenuAndList();
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(adapterCheckbox(menu, 'codex'));
    expect(visibleSessionNames(container)).toEqual(['codex-1']);

    fireEvent.click(adapterCheckbox(menu, 'codex'));
    expect(visibleSessionNames(container)).toEqual(['cbc-1', 'cbc-2', 'codex-1', 'kimi-1', 'none-1']);
  });

  it('two adapters are OR-ed in the list', () => {
    const { container } = renderMenuAndList();
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(adapterCheckbox(menu, 'codex'));
    fireEvent.click(adapterCheckbox(menu, 'kimi'));
    expect(visibleSessionNames(container)).toEqual(['codex-1', 'kimi-1']);
  });

  it('adapter and State filters combine with AND across groups', () => {
    const { container } = renderMenuAndList();
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(adapterCheckbox(menu, 'cbc'));
    fireEvent.click(stateCheckbox(menu, 'Running state'));
    // Only the RUNNING cbc session survives the cross-group AND.
    expect(visibleSessionNames(container)).toEqual(['cbc-2']);
  });

  it('an impossible combination shows the no-match empty state, clearing restores', () => {
    const { container } = renderMenuAndList();
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(adapterCheckbox(menu, 'kimi'));
    fireEvent.click(stateCheckbox(menu, 'Running state'));
    expect(screen.getByText('No matching sessions')).toBeTruthy();
    expect(visibleSessionNames(container)).toEqual([]);

    fireEvent.click(within(menu).getByRole('button', { name: 'Clear filters' }));
    expect(visibleSessionNames(container)).toEqual(['cbc-1', 'cbc-2', 'codex-1', 'kimi-1', 'none-1']);
  });

  it('the Idle state checkbox filters the list, ANDs with the adapter group, and follows live state changes', () => {
    useSessionStore.setState({
      sessions: [
        mk('idle-cbc', 'Idle cbc', { adapter: 'cbc', workerStatus: 'idle' }),
        mk('run-cbc', 'Run cbc', { adapter: 'cbc', workerStatus: 'running' }),
        mk('idle-codex', 'Idle codex', { adapter: 'codex', workerStatus: 'idle' }),
        mk('none', 'No status', { adapter: 'codex' }),
      ],
    });
    const { container } = renderMenuAndList();
    const menu = openMenu();

    // Idle display: only the two idle sessions survive; running / no status drop out.
    fireEvent.click(stateCheckbox(menu, 'Idle state'));
    expect(visibleSessionNames(container)).toEqual(['idle-cbc', 'idle-codex']);

    // Combined: AND with the adapter group → only the idle cbc session.
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(adapterCheckbox(menu, 'cbc'));
    expect(visibleSessionNames(container)).toEqual(['idle-cbc']);

    // Clear filters → everything is back (name order: the 'recent' tie-break).
    fireEvent.click(within(menu).getByRole('button', { name: 'Clear filters' }));
    expect(visibleSessionNames(container)).toEqual(['idle-cbc', 'idle-codex', 'none', 'run-cbc']);

    // State change: re-check Idle, then flip two worker statuses — the now-idle
    // session enters the list and the now-running one leaves it.
    fireEvent.click(stateCheckbox(menu, 'Idle state'));
    act(() => {
      useSessionStore.setState({
        sessions: [
          mk('idle-cbc', 'Idle cbc', { adapter: 'cbc', workerStatus: 'idle' }),
          mk('run-cbc', 'Run cbc', { adapter: 'cbc', workerStatus: 'idle' }),
          mk('idle-codex', 'Idle codex', { adapter: 'codex', workerStatus: 'running' }),
          mk('none', 'No status', { adapter: 'codex' }),
        ],
      });
    });
    expect(visibleSessionNames(container)).toEqual(['idle-cbc', 'run-cbc']);
  });

  it('select-mode select-all scope follows the adapter filter', () => {
    act(() => {
      useSessionStore.setState({ multiSelectMode: true });
    });
    const { container } = renderMenuAndList();
    const menu = openMenu();
    fireEvent.click(groupHeader(menu, 'adapter'));
    fireEvent.click(adapterCheckbox(menu, 'codex'));
    expect(visibleSessionNames(container)).toEqual(['codex-1']);
  });
});
