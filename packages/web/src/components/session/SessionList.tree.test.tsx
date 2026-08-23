// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { SessionList } from './SessionList';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

function mk(id: string, name: string, managedBy?: string | null): Session {
  return {
    id,
    name,
    managedBy,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
  };
}

describe('SessionList manager tree marker + stale-key pruning', () => {
  beforeEach(() => {
    useSessionStore.setState({ sessions: [], currentSessionId: null });
    useUIStore.setState({
      groupBy: 'manager',
      searchQuery: '',
      sortBy: 'recent',
      collapsedGroups: new Set(),
    });
  });

  it('renders a tree connector before each manager child session', () => {
    useSessionStore.setState({
      sessions: [mk('M', 'M'), mk('A', 'A', 'M'), mk('B', 'B', 'M')],
      currentSessionId: null,
    });
    const { container } = render(<SessionList />);

    // Manager children (A, B) get a tree tick; the root M does not.
    const ticks = container.querySelectorAll(
      '.ml-3.border-l span.absolute.-left-3',
    );
    expect(ticks.length).toBe(2);
    // Children stay in a normal horizontal flex layout (name + adapter + meta)
    expect(container.textContent).toContain('A');
    expect(container.textContent).toContain('B');
  });

  it('prunes stale collapsedGroups keys from removed placeholders/sessions', () => {
    useSessionStore.setState({
      sessions: [mk('M', 'M'), mk('A', 'A', 'M')],
      currentSessionId: null,
    });
    // Simulate a stale `__pending_X` key left over from a placeholder plus a
    // collapsed M (its id is still live).
    useUIStore.setState({
      collapsedGroups: new Set(['M', 'A', '__pending_X']),
    });
    render(<SessionList />);

    const keys = [...useUIStore.getState().collapsedGroups];
    expect(keys).toContain('M');
    expect(keys).not.toContain('__pending_X');
  });
});
