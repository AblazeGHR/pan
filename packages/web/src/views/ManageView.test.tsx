// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import ManageView from './ManageView';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

vi.mock('@/components/session/ManageModal', () => ({
  ManageSessionsPanel: () => <div data-testid="manage-panel">manage panel</div>,
}));

function LocationProbe() {
  return <div data-testid="location">{useLocation().pathname}</div>;
}

describe('ManageView mobile close behavior', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(max-width: 767px)',
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }));
    useSessionStore.setState({
      sessions: [
        {
          id: 'session-from-menu',
          name: 'Managed session',
          history: [],
          alwaysThinkingEnabled: false,
          effort: '',
        },
      ],
      currentSessionId: 'already-selected',
      loadSessions: vi.fn(async () => {}),
    });
    useUIStore.setState({ mobileSidebarOpen: false });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('closes with the top-right X, restores the mobile Sidebar, and does not select the managed session', async () => {
    render(
      <MemoryRouter initialEntries={['/manage/session-from-menu']}>
        <Routes>
          <Route path="/manage/:sessionId" element={<ManageView />} />
          <Route
            path="/"
            element={
              <>
                <div data-testid="chat-root">chat</div>
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId('manage-panel')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Close Manage Sessions' }));

    await waitFor(() => expect(screen.getByTestId('chat-root')).toBeTruthy());
    expect(screen.getByTestId('location').textContent).toBe('/');
    expect(useUIStore.getState().mobileSidebarOpen).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBe('already-selected');
  });
});
