// @vitest-environment jsdom
import { describe, it, expect, beforeEach } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
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

describe('SessionList manager grouping', () => {
  beforeEach(() => {
    useSessionStore.setState({ sessions: [], currentSessionId: null });
    useUIStore.setState({
      groupBy: 'manager',
      searchQuery: '',
      sortBy: 'recent',
      collapsedGroups: new Set(),
    });
  });

  it('builds nested A→B→C tree and collapses recursively', () => {
    // Chain: A managedBy B, B managedBy C. D is unmanaged (top-level).
    useSessionStore.setState({
      sessions: [mk('A', 'A', 'B'), mk('B', 'B', 'C'), mk('C', 'C'), mk('D', 'D')],
      currentSessionId: null,
    });

    const { container } = render(<SessionList />);

    // All four visible initially.
    expect(container.textContent).toContain('A');
    expect(container.textContent).toContain('B');
    expect(container.textContent).toContain('C');
    expect(container.textContent).toContain('D');

    // Nesting: two indented child containers — C wraps level-1 (B), which
    // wraps level-2 (A). D renders standalone (no children container).
    const nested = container.querySelectorAll('[data-tree-children]');
    expect(nested.length).toBe(2);
    const level1 = nested[0] as HTMLElement;
    const level2 = nested[1] as HTMLElement;
    expect(level1.contains(level2)).toBe(true);
    expect(level1.textContent).toContain('B');
    expect(level2.textContent).toContain('A');

    // Only managers with children get a collapse chevron (C and B).
    expect(container.querySelectorAll('button[title="Collapse group"]').length).toBe(2);

    // Collapse C → B and A disappear (recursive collapse).
    fireEvent.click(container.querySelector('button[title="Collapse group"]')!);
    expect(container.textContent).toContain('C');
    expect(container.textContent).toContain('D');
    expect(container.textContent).not.toContain('A');
    expect(container.textContent).not.toContain('B');
    expect(container.querySelector('button[title="Expand group"]')).toBeTruthy();

    // Expand C → entire subtree returns (recursive expand).
    fireEvent.click(container.querySelector('button[title="Expand group"]')!);
    expect(container.textContent).toContain('A');
    expect(container.textContent).toContain('B');
    expect(container.textContent).toContain('C');

    // Collapse only B → only A disappears; C and D stay.
    const collapseButtons = container.querySelectorAll('button[title="Collapse group"]');
    fireEvent.click(collapseButtons[1]!); // B's chevron (second in DOM order)
    expect(container.textContent).toContain('B');
    expect(container.textContent).toContain('C');
    expect(container.textContent).toContain('D');
    expect(container.textContent).not.toContain('A');
  });

  it('treats sessions whose manager is filtered out as top-level', () => {
    // A points at "ghost" manager not in the list → A becomes a root.
    useSessionStore.setState({
      sessions: [mk('A', 'A', 'ghost'), mk('B', 'B')],
      currentSessionId: null,
    });

    const { container } = render(<SessionList />);
    expect(container.querySelectorAll('.ml-3.border-l').length).toBe(0);
    expect(container.textContent).toContain('A');
    expect(container.textContent).toContain('B');
  });
});
