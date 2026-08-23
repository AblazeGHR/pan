// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { SessionList } from './SessionList';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import type { Session } from '@/types';

function mk(id: string, name: string, workdir?: string): Session {
  return {
    id,
    name,
    workdir,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
  };
}

describe('SessionList default group-by (app settings)', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
    useUIStore.setState({
      groupBy: 'none',
      searchQuery: '',
      sortBy: 'recent',
      collapsedGroups: new Set(),
    });
    useSessionStore.setState({ sessions: [], currentSessionId: null });
  });

  it('adopts the app-settings default on first render when nothing was persisted', () => {
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, defaultGroupBy: 'workdir' });
    useSessionStore.setState({
      sessions: [mk('a', 'A', 'D:/proj'), mk('b', 'B')],
      currentSessionId: null,
    });

    const { container } = render(<SessionList />);

    expect(useUIStore.getState().groupBy).toBe('workdir');
    // Grouped rendering actually kicked in: workdir group + uncategorized.
    expect(container.textContent).toContain('D:/proj');
    expect(container.textContent).toContain('No working directory');
  });

  it('does not override a grouping the user manually picked (pan:groupBy persisted)', () => {
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, defaultGroupBy: 'workdir' });
    localStorage.setItem('pan:groupBy', 'manager');
    useUIStore.setState({ groupBy: 'manager' });
    useSessionStore.setState({
      sessions: [mk('a', 'A', 'D:/proj'), mk('b', 'B')],
      currentSessionId: null,
    });

    render(<SessionList />);

    expect(useUIStore.getState().groupBy).toBe('manager');
  });

  it('respects a non-none groupBy that was set without persistence', () => {
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, defaultGroupBy: 'workdir' });
    useUIStore.setState({ groupBy: 'manager' });

    render(<SessionList />);

    expect(useUIStore.getState().groupBy).toBe('manager');
  });
});
