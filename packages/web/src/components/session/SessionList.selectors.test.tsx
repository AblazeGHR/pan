// @vitest-environment jsdom
import { Profiler } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';
import { SessionList } from './SessionList';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useAppSettingsStore, DEFAULT_SETTINGS } from '@/stores/appSettingsStore';
import type { Session } from '@/types';

function mk(id: string, name: string): Session {
  return { id, name, alwaysThinkingEnabled: false, effort: '', history: [] };
}

// FE-1: the list only subscribes to the UI slice it reads; toasts / interactive
// requests / other unrelated UI state must not re-render it.
describe('SessionList render isolation (fine-grained selectors)', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS });
    useSessionStore.setState({
      sessions: [mk('a', 'Alpha')],
      currentSessionId: null,
      selectedIds: new Set(),
      multiSelectMode: false,
    });
    useUIStore.setState({
      groupBy: 'none',
      searchQuery: '',
      sortBy: 'recent',
      specialFilters: new Set(),
      hiddenSessionIds: new Set(),
      collapsedGroups: new Set(),
      toastQueue: [],
      approvalRequests: [],
      userInputRequests: [],
      elicitationRequests: [],
      terminalInteractions: [],
    });
  });

  afterEach(() => cleanup());

  it('ignores unrelated UI-store updates but re-renders on a filter change', () => {
    const commits: number[] = [];
    render(
      <Profiler id="session-list" onRender={() => commits.push(1)}>
        <SessionList />
      </Profiler>,
    );
    const afterMount = commits.length;
    expect(afterMount).toBeGreaterThan(0);

    act(() => {
      useUIStore.setState({ toastQueue: [{ id: 't1', message: 'hi', type: 'info' }] });
      useUIStore.setState({
        approvalRequests: [
          { sessionId: 'a', workerId: 'w', requestId: 1, method: 'm', params: {} },
        ],
      });
      useUIStore.setState({
        terminalInteractions: [
          {
            sessionId: 'a',
            workerId: 'w',
            itemId: 'i1',
            processId: 'p1',
            stdin: '',
            params: {},
          },
        ],
      });
    });
    expect(commits.length).toBe(afterMount);

    act(() => {
      useUIStore.setState({ searchQuery: 'alp' });
    });
    expect(commits.length).toBeGreaterThan(afterMount);
  });
});
