// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('Sidebar Session search controls', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
    useSessionStore.setState({ sessions: [], currentSessionId: null, multiSelectMode: false });
    useUIStore.setState({
      sidebarCollapsed: false,
      searchQuery: '',
      specialFilters: new Set(),
      groupBy: 'none',
      sortBy: 'recent',
      dragEnabled: true,
    });
  });

  function renderSidebar() {
    return render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );
  }

  it('shows an accessible clear button only when the query is non-empty', () => {
    renderSidebar();
    expect(screen.queryByRole('button', { name: 'Clear session search' })).toBeNull();

    fireEvent.change(screen.getByPlaceholderText('Filter...'), {
      target: { value: 'cli-session' },
    });
    expect(screen.getByRole('button', { name: 'Clear session search' })).toBeTruthy();
  });

  it('clears the query and restores the unfiltered state', () => {
    useSessionStore.setState({
      sessions: [
        {
          id: 'pan-alpha',
          name: 'Alpha session',
          alwaysThinkingEnabled: false,
          effort: '',
          history: [],
        },
        {
          id: 'pan-beta',
          name: 'Beta session',
          alwaysThinkingEnabled: false,
          effort: '',
          history: [],
        },
      ],
    });
    useUIStore.setState({ searchQuery: 'alpha' });
    renderSidebar();

    expect(screen.getByText('Alpha session')).toBeTruthy();
    expect(screen.queryByText('Beta session')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Clear session search' }));

    expect(useUIStore.getState().searchQuery).toBe('');
    expect(screen.queryByRole('button', { name: 'Clear session search' })).toBeNull();
    expect(screen.getByText('Beta session')).toBeTruthy();
  });

  it('selects all filtered sessions, including sessions hidden in normal mode', () => {
    useSessionStore.setState({
      sessions: [
        { id: 'alpha', name: 'Alpha', alwaysThinkingEnabled: false, effort: '', history: [] },
        { id: 'beta', name: 'Beta', alwaysThinkingEnabled: false, effort: '', history: [] },
        { id: 'hidden', name: 'Hidden', alwaysThinkingEnabled: false, effort: '', history: [] },
      ],
      multiSelectMode: true,
      selectedIds: new Set(),
    });
    useUIStore.setState({ hiddenSessionIds: new Set(['hidden']), searchQuery: 'a' });
    renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: 'Select all visible sessions' }));

    expect(useSessionStore.getState().selectedIds).toEqual(new Set(['alpha', 'beta']));
    expect(screen.getByRole('button', { name: 'Deselect all visible sessions' })).toBeTruthy();
  });

  it('fills a partial selection and then deselects the filtered candidates', () => {
    useSessionStore.setState({
      sessions: [
        { id: 'alpha', name: 'Alpha', alwaysThinkingEnabled: false, effort: '', history: [] },
        { id: 'beta', name: 'Beta', alwaysThinkingEnabled: false, effort: '', history: [] },
      ],
      multiSelectMode: true,
      selectedIds: new Set(['alpha']),
    });
    renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: 'Select all visible sessions' }));
    expect(useSessionStore.getState().selectedIds).toEqual(new Set(['alpha', 'beta']));

    fireEvent.click(screen.getByRole('button', { name: 'Deselect all visible sessions' }));
    expect(useSessionStore.getState().selectedIds).toEqual(new Set());
  });

  it('uses special filters for the select-all candidate range', () => {
    useSessionStore.setState({
      sessions: [
        { id: 'meta', name: 'Meta', alwaysThinkingEnabled: false, effort: '', history: [], mcpServers: ['pan'] },
        { id: 'plain', name: 'Plain', alwaysThinkingEnabled: false, effort: '', history: [] },
      ],
      multiSelectMode: true,
      selectedIds: new Set(),
    });
    useUIStore.setState({ specialFilters: new Set(['metaagent']) });
    renderSidebar();

    fireEvent.click(screen.getByRole('button', { name: 'Select all visible sessions' }));

    expect(useSessionStore.getState().selectedIds).toEqual(new Set(['meta']));
  });

  it('disables select all when the filtered candidate list is empty', () => {
    useSessionStore.setState({
      sessions: [{ id: 'plain', name: 'Plain', alwaysThinkingEnabled: false, effort: '', history: [] }],
      multiSelectMode: true,
      selectedIds: new Set(),
    });
    useUIStore.setState({ searchQuery: 'does-not-match' });
    renderSidebar();

    expect((screen.getByRole('button', { name: 'Select all visible sessions' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('defaults drag sorting on and hides the handle when switched off', () => {
    useSessionStore.setState({
      sessions: [{ id: 'alpha', name: 'Alpha', alwaysThinkingEnabled: false, effort: '', history: [] }],
    });
    renderSidebar();

    expect(screen.getByTestId('drag-handle')).toBeTruthy();
    expect(useUIStore.getState().dragEnabled).toBe(true);

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Sort sessions: recent' }), {
      pointerType: 'touch', clientX: 10, clientY: 10,
    });
    act(() => vi.advanceTimersByTime(600));
    expect(screen.getByRole('menu', { name: 'Session list options' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitemcheckbox'));

    expect(useUIStore.getState().dragEnabled).toBe(false);
    expect(screen.queryByTestId('drag-handle')).toBeNull();
    expect(localStorage.getItem('pan:dragEnabled')).toBe('false');
  });

  it('opens the drag menu on long press without cycling sort', () => {
    vi.useFakeTimers();
    renderSidebar();
    const sort = screen.getByRole('button', { name: 'Sort sessions: recent' });
    fireEvent.pointerDown(sort, { pointerType: 'mouse', button: 0, clientX: 10, clientY: 10 });
    act(() => vi.advanceTimersByTime(600));
    fireEvent.pointerUp(sort, { pointerType: 'mouse', button: 0, clientX: 10, clientY: 10 });
    fireEvent.click(sort);

    expect(screen.getByRole('menu', { name: 'Session list options' })).toBeTruthy();
    expect(useUIStore.getState().sortBy).toBe('recent');
    vi.useRealTimers();
  });

  it('keeps a short press cycling sort', () => {
    renderSidebar();
    const sort = screen.getByRole('button', { name: 'Sort sessions: recent' });
    fireEvent.pointerDown(sort, { pointerType: 'mouse', button: 0, clientX: 10, clientY: 10 });
    fireEvent.pointerUp(sort, { pointerType: 'mouse', button: 0, clientX: 10, clientY: 10 });
    fireEvent.click(sort);
    expect(useUIStore.getState().sortBy).toBe('name');
  });
});
