// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

afterEach(() => cleanup());

describe('Sidebar Session search controls', () => {
  beforeEach(() => {
    localStorage.clear();
    useSessionStore.setState({ sessions: [], currentSessionId: null, multiSelectMode: false });
    useUIStore.setState({
      sidebarCollapsed: false,
      searchQuery: '',
      specialFilters: new Set(),
      groupBy: 'none',
      sortBy: 'recent',
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
});
