// @vitest-environment jsdom
// Select-mode bulk Manage / msgBridge entry points.
//
// The bulk windows must:
//   - read the representative state from the FIRST selected Session in the
//     stable (list) order,
//   - write nothing before the explicit "Apply to all selected sessions"
//     action,
//   - apply only non-manages-session fields through the same single-Session
//     APIs (so permissions/tree/CAS rules stay authoritative),
//   - roll back already-applied members when one member fails.
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { render, fireEvent, cleanup, act } from '@testing-library/react';
import { SelectedSessionActions } from './SelectedSessionActions';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkerStore } from '@/stores/workerStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { Session } from '@/types';

const apiMocks = vi.hoisted(() => ({
  fetchSessions: vi.fn(),
  fetchSession: vi.fn(),
  fetchMcpServers: vi.fn(),
  patchSession: vi.fn(),
  claimSession: vi.fn(),
  unclaimSession: vi.fn(),
  setSessionReadonly: vi.fn(),
  setReportsToManager: vi.fn(),
  setSessionMsgBridge: vi.fn(),
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    fetchSessions: apiMocks.fetchSessions,
    fetchSession: apiMocks.fetchSession,
    fetchMcpServers: apiMocks.fetchMcpServers,
    patchSession: apiMocks.patchSession,
    claimSession: apiMocks.claimSession,
    unclaimSession: apiMocks.unclaimSession,
    setSessionReadonly: apiMocks.setSessionReadonly,
    setReportsToManager: apiMocks.setReportsToManager,
    setSessionMsgBridge: apiMocks.setSessionMsgBridge,
  };
});

function mk(id: string, name: string, extra: Partial<Session> = {}): Session {
  return { id, name, alwaysThinkingEnabled: false, effort: '', history: [], ...extra };
}

/** Full metadata as returned by GET /api/sessions/:id. */
function seedSessions(): Session[] {
  return [
    mk('a', 'Alpha', {
      managedBy: 'm',
      panAccess: { restrictToManaged: true },
      mcpServers: ['pan'],
      msgBridgeEnabled: true,
      notificationSettings: { system: true, browser: false },
      readonlySession: false,
      reportsToManager: true,
    }),
    mk('b', 'Bravo', {
      panAccess: {},
      mcpServers: [],
      msgBridgeEnabled: true,
      notificationSettings: { system: false, browser: false },
      readonlySession: false,
    }),
    mk('m', 'Manager'),
  ];
}

function seedStores(): void {
  useSessionStore.setState({
    sessions: seedSessions(),
    // Insertion order deliberately differs from the list order: the
    // representative must follow the stable list order (Alpha), not Set order.
    multiSelectMode: true,
    selectedIds: new Set(['b', 'a']),
  });
  useWorkerStore.setState({ workers: {}, delayedRestarts: {} });
  useUIStore.setState({ toastQueue: [] });
  useWorkspaceStore.setState({ workspaces: [], loaded: true });
}

function openMenu(): void {
  fireEvent.click(document.querySelector('button[aria-haspopup="menu"]') as HTMLButtonElement);
}

function menuItem(name: string): HTMLButtonElement {
  const item = [...document.querySelectorAll('button[role="menuitem"]')].find(
    (element) => element.textContent?.trim() === name,
  );
  if (!item) throw new Error(`Menu item ${name} not found`);
  return item as HTMLButtonElement;
}

function applyButton(): HTMLButtonElement {
  return document.querySelector('button[data-testid="bulk-apply"]') as HTMLButtonElement;
}

function toastMessages(): string[] {
  return useUIStore.getState().toastQueue.map((toast) => toast.message);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  seedStores();
  const sessions = seedSessions();
  apiMocks.fetchSessions.mockImplementation(async () => sessions.map((session) => ({ ...session })));
  apiMocks.fetchSession.mockImplementation(async (id: string) => {
    const session = sessions.find((item) => item.id === id);
    if (!session) throw new Error('Session not found');
    return { ...session };
  });
  apiMocks.fetchMcpServers.mockResolvedValue([{ name: 'pan' }, { name: 'qq' }]);
  apiMocks.patchSession.mockImplementation(async (id: string, body: Record<string, unknown>) => {
    const session = sessions.find((item) => item.id === id);
    return { ...(session ?? mk(id, id)), ...body };
  });
  apiMocks.claimSession.mockResolvedValue({ ok: true });
  apiMocks.unclaimSession.mockResolvedValue({ ok: true });
  apiMocks.setSessionReadonly.mockResolvedValue({ ok: true });
  apiMocks.setReportsToManager.mockResolvedValue({ ok: true, reportsToManager: true });
  apiMocks.setSessionMsgBridge.mockResolvedValue({ ok: true, msgBridgeEnabled: false });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SelectedSessionActions bulk Manage', () => {
  it('exposes a Manage bulk entry in select mode', () => {
    render(<SelectedSessionActions />);
    openMenu();
    expect(menuItem('Manage')).toBeTruthy();
    expect(menuItem('MsgBridge')).toBeTruthy();
  });

  it('reads the first stable-ordered representative and writes nothing before confirm', async () => {
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Manage'));
    });

    expect(document.querySelector('[data-testid="bulk-manage-modal"]')).not.toBeNull();
    expect(document.body.textContent).toContain('Representative: Alpha');
    expect(document.body.textContent).toContain('2 selected Sessions');
    // Representative state (pan_mcp) is visible before any write.
    expect((document.querySelector('input[aria-label="MCP: pan"]') as HTMLInputElement).checked).toBe(true);
    expect(applyButton().textContent).toContain('Apply to all selected sessions');

    expect(apiMocks.patchSession).not.toHaveBeenCalled();
    expect(apiMocks.claimSession).not.toHaveBeenCalled();
    expect(apiMocks.unclaimSession).not.toHaveBeenCalled();
    expect(apiMocks.setSessionReadonly).not.toHaveBeenCalled();
    expect(apiMocks.setReportsToManager).not.toHaveBeenCalled();
  });

  it('does not offer a selected Session or its descendant as the bulk manager', async () => {
    apiMocks.fetchSession.mockImplementation(async (id: string) => {
      const session = useSessionStore.getState().sessions.find((item) => item.id === id);
      if (!session) throw new Error('Session not found');
      return { ...session };
    });
    useSessionStore.setState({
      sessions: [
        ...seedSessions(),
        mk('c', 'Child', { managedBy: 'a' }),
      ],
      multiSelectMode: true,
      selectedIds: new Set(['a', 'b']),
    });
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Manage'));
    });
    const select = document.querySelector('select[aria-label="Managed by"]') as HTMLSelectElement;
    const values = [...select.options].map((option) => option.value);
    expect(values).toContain('m');
    expect(values).not.toContain('a'); // selected target itself
    expect(values).not.toContain('c'); // descendant of a selected target
  });

  it('never renders the manages-session (children) list', async () => {
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Manage'));
    });
    expect(document.querySelector('[data-testid="bulk-managed-sessions"]')).toBeNull();
    expect(document.body.textContent).not.toContain('Managed sessions');
  });

  it('applies non-manages fields to every selected Session after confirm', async () => {
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Manage'));
    });
    const restrict = document.querySelector('input[aria-label="Restrict to managed"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.click(restrict);
    });
    await act(async () => {
      fireEvent.click(applyButton());
    });

    // Alpha (representative) turns the flag off…
    expect(apiMocks.patchSession).toHaveBeenCalledWith('a', {
      panAccess: { restrictToManaged: false },
    });
    // …and Bravo receives the representative's whole state: its Pan access
    // still resolves to false and its MCP selection now matches Alpha.
    expect(apiMocks.patchSession).toHaveBeenCalledWith('b', {
      mcpServers: ['pan'],
    });
    for (const [, body] of apiMocks.patchSession.mock.calls as [string, Record<string, unknown>][]) {
      expect(Object.prototype.hasOwnProperty.call(body, 'managed')).toBe(false);
    }
    expect(toastMessages().some((message) => message.includes('2 selected Sessions'))).toBe(true);
  });

  it('reuses the single-Session manager rules (claim the changed manager only)', async () => {
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Manage'));
    });
    await act(async () => {
      fireEvent.click(applyButton());
    });

    // Alpha is already managed by Manager; only Bravo changes.
    expect(apiMocks.claimSession).toHaveBeenCalledWith('m', 'b');
    expect(apiMocks.claimSession).not.toHaveBeenCalledWith('m', 'a');
  });

  it('applies to a selected manager without asking it to manage itself', async () => {
    apiMocks.fetchSession.mockImplementation(async (id: string) => {
      const session = useSessionStore.getState().sessions.find((item) => item.id === id);
      if (!session) throw new Error('Session not found');
      return { ...session };
    });
    useSessionStore.setState({
      sessions: seedSessions(),
      multiSelectMode: true,
      selectedIds: new Set(['a', 'm']),
    });
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Manage'));
    });
    await act(async () => {
      fireEvent.click(applyButton());
    });

    expect(apiMocks.claimSession).not.toHaveBeenCalled();
    expect(apiMocks.setReportsToManager).not.toHaveBeenCalled();
    expect(apiMocks.patchSession).toHaveBeenCalledWith('m', {
      panAccess: { restrictToManaged: true },
    });
    expect(toastMessages()).toContain('Updated 2 selected Sessions');
  });

  it('rolls back earlier members when a later member fails', async () => {
    apiMocks.patchSession.mockImplementation(async (id: string, body: Record<string, unknown>) => {
      if (id === 'b') throw new Error('Server rejected settings');
      const session = seedSessions().find((item) => item.id === id);
      return { ...(session ?? mk(id, id)), ...body };
    });
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('Manage'));
    });
    const restrict = document.querySelector('input[aria-label="Restrict to managed"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.click(restrict);
    });
    await act(async () => {
      fireEvent.click(applyButton());
    });

    // Alpha was patched first, then Bravo failed → Alpha is restored.
    expect(apiMocks.patchSession).toHaveBeenNthCalledWith(1, 'a', {
      panAccess: { restrictToManaged: false },
    });
    expect(apiMocks.patchSession).toHaveBeenLastCalledWith('a', {
      panAccess: { restrictToManaged: true },
    });
    const errors = useUIStore.getState().toastQueue.filter((toast) => toast.type === 'error');
    expect(errors.length).toBe(1);
    expect(errors[0]!.message).toContain('rolled back');
    expect(toastMessages().some((message) => message.includes('2 selected Sessions'))).toBe(false);
  });
});

describe('SelectedSessionActions bulk MsgBridge', () => {
  it('reads the representative msgBridge state and writes nothing before confirm', async () => {
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('MsgBridge'));
    });
    expect(document.querySelector('[data-testid="bulk-msgbridge-modal"]')).not.toBeNull();
    expect(document.body.textContent).toContain('Representative: Alpha');
    const master = document.querySelector('input[aria-label="msgBridge enabled"]') as HTMLInputElement;
    expect(master.checked).toBe(true);
    expect(apiMocks.setSessionMsgBridge).not.toHaveBeenCalled();
    expect(apiMocks.patchSession).not.toHaveBeenCalled();
  });

  it('applies the confirmed msgBridge value to every selected Session', async () => {
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('MsgBridge'));
    });
    const master = document.querySelector('input[aria-label="msgBridge enabled"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.click(master);
    });
    await act(async () => {
      fireEvent.click(applyButton());
    });
    expect(apiMocks.setSessionMsgBridge).toHaveBeenCalledWith('a', false);
    expect(apiMocks.setSessionMsgBridge).toHaveBeenCalledWith('b', false);
  });

  it('rolls back the msgBridge change when a later member fails', async () => {
    apiMocks.setSessionMsgBridge.mockImplementation(async (id: string) => {
      if (id === 'b') throw new Error('msgBridge rejected');
      return { ok: true, msgBridgeEnabled: false };
    });
    render(<SelectedSessionActions />);
    openMenu();
    await act(async () => {
      fireEvent.click(menuItem('MsgBridge'));
    });
    const master = document.querySelector('input[aria-label="msgBridge enabled"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.click(master);
    });
    await act(async () => {
      fireEvent.click(applyButton());
    });

    expect(apiMocks.setSessionMsgBridge).toHaveBeenNthCalledWith(1, 'a', false);
    // Alpha returns to its original value.
    expect(apiMocks.setSessionMsgBridge).toHaveBeenLastCalledWith('a', true);
    expect(
      useUIStore.getState().toastQueue.some((toast) => toast.type === 'error' && toast.message.includes('rolled back')),
    ).toBe(true);
  });

  it('stays usable on a narrow viewport', async () => {
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      matches: query.includes('max-width: 767px'),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
    try {
      render(<SelectedSessionActions />);
      openMenu();
      await act(async () => {
        fireEvent.click(menuItem('Manage'));
      });
      expect(applyButton().textContent).toContain('Apply to all selected sessions');
      expect(document.querySelector('[data-testid="bulk-manage-modal"]')).not.toBeNull();
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });
});
