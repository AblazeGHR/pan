// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, cleanup, act } from '@testing-library/react';
import { SessionList } from './SessionList';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

function mk(id: string, name: string, extra: Partial<Session> = {}): Session {
  return { id, name, alwaysThinkingEnabled: false, effort: '', history: [], ...extra };
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

/** Card row container selector (same shape as SessionItem's root div). */
function rows(container: HTMLElement): NodeListOf<HTMLElement> {
  return container.querySelectorAll('.flex.items-center.gap-2.px-3.py-2');
}

function enterSelectMode() {
  act(() => {
    useSessionStore.setState({ multiSelectMode: true, selectedIds: new Set() });
  });
}

describe('SessionList select-mode interactions', () => {
  beforeEach(() => {
    resetStores();
  });

  afterEach(() => {
    cleanup();
  });

  it('select mode keeps the expand/collapse chevron clickable (manager tree)', () => {
    useSessionStore.setState({
      sessions: [mk('parent', 'Parent'), mk('child', 'Child', { managedBy: 'parent' })],
    });
    useUIStore.setState({ groupBy: 'manager' });

    const { container } = render(<SessionList />);
    expect(container.querySelector('[data-session-card-id="child"]')).not.toBeNull();

    enterSelectMode();

    // The chevron must still be rendered in select mode.
    const chevron = container.querySelector('button[title="Collapse group"]');
    expect(chevron).not.toBeNull();

    // Clicking it collapses the group without opening a session or toggling
    // the selection.
    fireEvent.click(chevron!);
    expect(useUIStore.getState().collapsedGroups.has('parent')).toBe(true);
    expect(container.querySelector('[data-session-card-id="child"]')).toBeNull();
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(useSessionStore.getState().selectedIds.size).toBe(0);

    // And it expands again.
    fireEvent.click(container.querySelector('button[title="Expand group"]')!);
    expect(useUIStore.getState().collapsedGroups.has('parent')).toBe(false);
    expect(container.querySelector('[data-session-card-id="child"]')).not.toBeNull();
  });

  it('checkbox toggles selection without opening the session', () => {
    useSessionStore.setState({ sessions: [mk('a', 'Alpha'), mk('b', 'Beta')] });

    const { container } = render(<SessionList />);
    enterSelectMode();

    const checkbox = container.querySelector('input[type="checkbox"]') as HTMLInputElement;
    // Native input → keyboard togglable, labeled for screen readers.
    expect(checkbox.disabled).toBe(false);
    expect(checkbox.getAttribute('aria-label')).toBe('Select Alpha');

    fireEvent.click(checkbox);
    expect(useSessionStore.getState().selectedIds.has('a')).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBeNull();

    fireEvent.click(checkbox);
    expect(useSessionStore.getState().selectedIds.has('a')).toBe(false);
  });

  it('clicking the card body opens the session instead of changing selection', () => {
    useSessionStore.setState({ sessions: [mk('a', 'Alpha'), mk('b', 'Beta')] });

    const { container } = render(<SessionList />);
    enterSelectMode();

    // Pre-select Beta via its checkbox so we can prove the click on Alpha's
    // body leaves the selection untouched.
    const checkboxes = container.querySelectorAll('input[type="checkbox"]');
    fireEvent.click(checkboxes[1]!);
    expect(useSessionStore.getState().selectedIds.has('b')).toBe(true);

    fireEvent.click(rows(container)[0]!);
    expect(useSessionStore.getState().currentSessionId).toBe('a');
    expect(useSessionStore.getState().selectedIds.has('b')).toBe(true);
    expect(useSessionStore.getState().selectedIds.has('a')).toBe(false);
  });

  it('checkbox hit zone is enlarged and still toggles exactly once per click', () => {
    useSessionStore.setState({ sessions: [mk('a', 'Alpha')] });

    const { container } = render(<SessionList />);
    enterSelectMode();

    // Full-height generous zone: the label stretches beyond the card padding.
    const zone = container.querySelector('[data-testid="select-checkbox-zone"]') as HTMLElement;
    expect(zone).not.toBeNull();
    expect(zone.className).toContain('-my-2');
    expect(zone.className).toContain('p-2');

    // A click anywhere in the zone (not on the bare input) toggles selection
    // once via label→input forwarding and does NOT open the session.
    fireEvent.click(zone);
    expect(useSessionStore.getState().selectedIds.has('a')).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBeNull();

    fireEvent.click(zone);
    expect(useSessionStore.getState().selectedIds.has('a')).toBe(false);
  });

  it('eye (hide) button hit area is enlarged and keeps its stopPropagation', () => {
    useSessionStore.setState({ sessions: [mk('a', 'Alpha')] });

    const { container } = render(<SessionList />);
    enterSelectMode();

    const eye = container.querySelector('button[title="Hide session"]') as HTMLElement;
    expect(eye).not.toBeNull();
    // Enlarged beyond the old p-1: full-height padded tap target.
    expect(eye.className).toContain('p-2');
    expect(eye.className).toContain('-my-2');

    // Behavior unchanged: hides the session, does not open/select the card.
    fireEvent.click(eye);
    expect(useUIStore.getState().hiddenSessionIds.has('a')).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBeNull();
    expect(useSessionStore.getState().selectedIds.size).toBe(0);
  });
});
