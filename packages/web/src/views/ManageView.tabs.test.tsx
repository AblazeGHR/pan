// @vitest-environment jsdom
// The mobile Manage page renders the SAME panel as the desktop modal, so it
// must expose the same four tabs — and keep working with the real panel
// (ManageView.test.tsx stubs the panel out to test close behaviour only).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ManageView from './ManageView';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import type { McpServerInfo, Session, Workspace } from '@/types';

const mobileSession: Session = {
  id: 'session-mobile',
  name: 'Mobile session',
  alwaysThinkingEnabled: false,
  effort: '',
  history: [],
  managed: [],
  managedBy: null,
};

const apiMock = vi.hoisted(() => ({
  fetchSession: vi.fn(),
  fetchMcpServers: vi.fn(async (): Promise<McpServerInfo[]> => []),
  fetchWorkspaces: vi.fn(async (): Promise<Workspace[]> => []),
  setSessionWorkspaces: vi.fn(async () => ({ ok: true })),
  claimSession: vi.fn(async () => ({ ok: true })),
  unclaimSession: vi.fn(async () => ({ ok: true })),
  reportSubscribe: vi.fn(async () => ({})),
  reportUnsubscribe: vi.fn(async () => ({})),
  setSessionReadonly: vi.fn(async () => ({ ok: true })),
  patchSession: vi.fn(),
}));

vi.mock('@/services/api', () => apiMock);

describe('ManageView mobile tabs', () => {
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
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
    useUIStore.setState({ mobileSidebarOpen: false, toastQueue: [] });
    useWorkspaceStore.setState({ workspaces: [], loaded: false, loading: false, error: null });
    useSessionStore.setState({
      sessions: [mobileSession],
      // Deliberately not the managed session: closing must not select it.
      currentSessionId: 'already-selected',
      loadSessions: vi.fn(async () => {}),
    });
    apiMock.fetchSession.mockResolvedValue({ ...mobileSession, reportSubscriptions: [] });
    apiMock.fetchMcpServers.mockResolvedValue([{ name: 'pan', command: 'node pan.js' }]);
    apiMock.fetchWorkspaces.mockResolvedValue([{ id: 'w-a', name: 'Alpha', order: null }]);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  function renderManageView() {
    return render(
      <MemoryRouter initialEntries={['/manage/session-mobile']}>
        <Routes>
          <Route path="/manage/:sessionId" element={<ManageView />} />
          <Route path="/" element={<div data-testid="chat-root">chat</div>} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it('shares the four Manage tabs and keeps closing the page intact', async () => {
    renderManageView();

    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Relationship',
      'Workspaces',
      'Access',
      'MCP and Plugins',
    ]);
    expect(screen.getByRole('tab', { name: 'Relationship' }).getAttribute('aria-selected')).toBe('true');
    expect(await screen.findByText('Managed by')).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: 'Workspaces' }));
    expect(await screen.findByRole('button', { name: 'Alpha' })).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: 'Access' }));
    expect(screen.getByText('Pan Access')).toBeTruthy();
    expect(screen.queryByText(/MCP 权限/)).toBeNull();

    fireEvent.click(screen.getByRole('tab', { name: 'MCP and Plugins' }));
    expect(await screen.findByText('pan')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Close Manage Sessions' }));
    await waitFor(() => expect(screen.getByTestId('chat-root')).toBeTruthy());
    expect(useUIStore.getState().mobileSidebarOpen).toBe(true);
    expect(useSessionStore.getState().currentSessionId).toBe('already-selected');
  });
});
