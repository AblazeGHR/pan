// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Sidebar } from '@/components/layout/Sidebar';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import type { Session } from '@/types';

const sessions: Session[] = [
  { id: 'a', name: 'Session A', alwaysThinkingEnabled: false, effort: '', history: [] },
];

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({
    rename: vi.fn().mockResolvedValue(undefined),
    sessions,
    currentSessionId: null,
    multiSelectMode: false,
    selectedIds: new Set(),
  });
  useUIStore.setState({
    sidebarCollapsed: false,
    mobileSidebarOpen: false,
    searchQuery: '',
    specialFilters: new Set(),
    groupBy: 'none',
    sortBy: 'recent',
    dragEnabled: false,
  });
  useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('Rename entry from the real session card menu', () => {
  it('hands focus and select-all to the input when the menu item opens the dialog', () => {
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );

    // Real entry path: card actions menu -> Rename.
    fireEvent.click(screen.getByTitle('Session actions'));
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));

    const input = screen.getByRole('textbox', { name: 'Session name' }) as HTMLInputElement;
    expect(input.value).toBe('Session A');
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe('Session A'.length);
    // The menu portal closed and handed off to the dialog.
    expect(screen.getByRole('dialog', { name: 'Rename Session' })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Rename' })).toHaveLength(1);
  });
});
