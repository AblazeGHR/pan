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
      '.ml-3.border-l span.absolute.-left-px',
    );
    expect(ticks.length).toBe(2);
    // Children stay in a normal horizontal flex layout (name + adapter + meta)
    expect(container.textContent).toContain('A');
    expect(container.textContent).toContain('B');
  });

  it('renders connectors for nested levels (A→B→C) without overlap', () => {
    useSessionStore.setState({
      sessions: [mk('A', 'A'), mk('B', 'B', 'A'), mk('C', 'C', 'B')],
      currentSessionId: null,
    });
    const { container } = render(<SessionList />);

    // Every child row — B and C — gets exactly one connector tick, and each
    // lives inside its own level's `.ml-3.border-l` container.
    const containers = container.querySelectorAll('.ml-3.border-l');
    expect(containers.length).toBe(2); // A's children, B's children

    const ticks = container.querySelectorAll(
      '.ml-3.border-l span.absolute.-left-px',
    );
    expect(ticks.length).toBe(2);

    // Nesting: A's children container holds B (which holds C's container).
    const level1 = containers[0] as HTMLElement;
    const level2 = containers[1] as HTMLElement;
    expect(level1.contains(level2)).toBe(true);
    // C's tick is inside B's children container (level2), not level1.
    expect(ticks[1] ? level2.contains(ticks[1]) : false).toBe(true);
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
