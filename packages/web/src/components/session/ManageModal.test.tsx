// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
  cleanup,
  act,
} from '@testing-library/react';
import { ManageModal } from './ManageModal';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { DEFAULT_SETTINGS, useAppSettingsStore } from '@/stores/appSettingsStore';
import type { McpServerInfo, Session, Workspace } from '@/types';

const apiMock = vi.hoisted(() => ({
  fetchSession: vi.fn(),
  claimSession: vi.fn(async () => ({ ok: true })),
  unclaimSession: vi.fn(async () => ({ ok: true })),
  reportSubscribe: vi.fn(async () => ({})),
  reportUnsubscribe: vi.fn(async () => ({})),
  fetchMcpServers: vi.fn(async (): Promise<McpServerInfo[]> => []),
  patchSession: vi.fn(),
  setSessionReadonly: vi.fn(async () => ({ ok: true, readonlySession: true })),
  fetchWorkspaces: vi.fn(async (): Promise<Workspace[]> => []),
  setSessionWorkspaces: vi.fn(async () => ({ ok: true })),
}));

vi.mock('@/services/api', () => apiMock);

function mk(id: string, name: string, extra?: Partial<Session>): Session {
  return {
    id,
    name,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
    ...extra,
  };
}

function mkWorkspace(id: string, name: string): Workspace {
  return { id, name, order: null };
}

/** Switch to one of the four Manage tabs. */
function openTab(name: string) {
  fireEvent.click(screen.getByRole('tab', { name }));
}

/** Section order inside the ACTIVE tab panel (Relationship is the default). */
function section(i: number) {
  const el = screen.getByRole('tabpanel').querySelectorAll('section')[i];
  return within(el as HTMLElement);
}

/**
 * Seeds a manager tree where the CHILD is the opened session: the child keeps
 * no membership of its own and inherits `Boss`'s workspace (w-a) instead.
 */
function seedManagedChild(extra?: { confirmation?: boolean }) {
  useSessionStore.setState({
    sessions: [
      mk('mgr', 'Boss', { workspaceIds: ['w-a'] }),
      mk('child', 'Child', { managedBy: 'mgr', workspaceIds: [] }),
    ],
    currentSessionId: 'child',
    loadSessions: vi.fn(async () => {}),
  });
  useWorkspaceStore.setState({
    workspaces: [mkWorkspace('w-a', 'Alpha'), mkWorkspace('w-b', 'Beta')],
    loaded: true,
    loading: false,
    error: null,
  });
  if (extra?.confirmation === false) {
    useAppSettingsStore.setState({
      ...DEFAULT_SETTINGS,
      loaded: true,
      notifications: { ...DEFAULT_SETTINGS.notifications, confirmCrossWorkspaceManagement: false },
    });
  }
  apiMock.fetchSession.mockResolvedValue(
    mk('child', 'Child', { managedBy: 'mgr', managed: [], reportSubscriptions: [] }),
  );
}

describe('ManageModal', () => {
  // The vitest config has no globals, so RTL auto-cleanup is off — the modal
  // renders through a portal to <body> and would leak into the next test.
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    useAppSettingsStore.setState({ ...DEFAULT_SETTINGS, loaded: true });
    useUIStore.setState({ toastQueue: [] });
    useWorkspaceStore.setState({ workspaces: [], loaded: false, loading: false, error: null });
    useSessionStore.setState({
      sessions: [mk('s1', 'Child', { managedBy: 'mgr' }), mk('mgr', 'Boss')],
      currentSessionId: 's1',
      loadSessions: vi.fn(async () => {}),
    });
  });

  it('cancels cross-workspace management from ManageModal before calling claim', async () => {
    useSessionStore.setState({
      sessions: [
        mk('mgr', 'Manager', { workspaceIds: ['workspace-b'], managed: [] }),
        mk('target', 'Target', { workspaceIds: ['workspace-a'] }),
      ],
      currentSessionId: 'mgr',
      loadSessions: vi.fn(async () => {}),
    });
    apiMock.fetchSession.mockResolvedValue(
      mk('mgr', 'Manager', { workspaceIds: ['workspace-b'], managed: [], reportSubscriptions: [] }),
    );
    const confirmation = new Promise<void>((resolve) => {
      window.addEventListener('pan:confirm-workspace-manager-change', ((event: Event) => {
        const detail = (event as CustomEvent<{ changeType: string; resolve: (accepted: boolean) => void }>).detail;
        expect(detail.changeType).toBe('attach');
        detail.resolve(false);
        resolve();
      }) as EventListener, { once: true });
    });

    render(<ManageModal open onClose={() => {}} sessionId="mgr" />);
    fireEvent.click(await section(1).findByRole('button', { name: 'Manage' }));

    await confirmation;
    expect(apiMock.claimSession).not.toHaveBeenCalled();
    expect(apiMock.unclaimSession).not.toHaveBeenCalled();
  });

  it('shows the managing session and detaches it via unclaim', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Child', { managedBy: 'mgr', managed: [], reportSubscriptions: [] }),
    );

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    // Section 1 resolves managedBy → the manager's name + id.
    const cancel = await screen.findByTitle(/Break the manage link/);
    expect(section(0).getByText('Boss')).toBeTruthy();
    expect(section(0).getByText('mgr')).toBeTruthy();

    // Detaching passes the *current manager* as managerId (backend only checks
    // that it matches managed_by, so the managed session may break the link).
    fireEvent.click(cancel);
    await waitFor(() =>
      expect(apiMock.unclaimSession).toHaveBeenCalledWith('mgr', 's1'),
    );
    // Unmanage must not be mislabeled as a report toggle, and only unclaim is
    // allowed to drop the manage link.
    expect(apiMock.reportUnsubscribe).not.toHaveBeenCalled();
    expect(await screen.findByText('Unmanaged')).toBeTruthy();
    expect(screen.queryByTitle(/Break the manage link/)).toBeNull();
  });

  // fetchSession resolves the panel's session ("s1") and its manager ("mgr")
  // separately so section 1 can mirror the manager's row controls for s1.
  function mockManagedByParent(extra?: Partial<Session>) {
    apiMock.fetchSession.mockImplementation(async (id: string) => {
      if (id === 'mgr') {
        return mk('mgr', 'Boss', {
          managed: ['s1'],
          reportSubscriptions: ['s1'],
        });
      }
      return mk('s1', 'Child', {
        managedBy: 'mgr',
        managed: [],
        reportSubscriptions: [],
        ...extra,
      });
    });
  }

  it('mirrors the manager row for a managed session with concise English actions', async () => {
    mockManagedByParent();

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    const box = section(0);
    expect(await screen.findByTitle(/Break the manage link/)).toBeTruthy();
    expect(box.getByText('Boss')).toBeTruthy();
    expect(box.getByText('mgr')).toBeTruthy();
    // The manager auto-subscribes on claim, so "Stop reports" is the active state.
    expect(box.getByRole('button', { name: 'Stop reports' })).toBeTruthy();
    const readonly = box.getByRole('button', { name: 'Readonly' });
    expect(readonly.getAttribute('aria-pressed')).toBe('false');
    expect(box.queryByText('Unmanaged')).toBeNull();
  });

  it('stops reports without breaking management (report-unsubscribe only)', async () => {
    mockManagedByParent();

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    const stop = await screen.findByRole('button', { name: 'Stop reports' });
    fireEvent.click(stop);
    await waitFor(() =>
      expect(apiMock.reportUnsubscribe).toHaveBeenCalledWith('mgr', 's1'),
    );
    // Only report-unsubscribe ran: management must stay intact.
    expect(apiMock.unclaimSession).not.toHaveBeenCalled();
    expect(apiMock.reportSubscribe).not.toHaveBeenCalled();

    // Still managed: the manager stays listed and the button flips so the user
    // can resume reports.
    const box = section(0);
    expect(box.getByText('Boss')).toBeTruthy();
    expect(box.queryByText('Unmanaged')).toBeNull();
    expect(await screen.findByRole('button', { name: 'Start reports' })).toBeTruthy();

    // Resuming reports re-subscribes (mirror of the manager's Subscribe action).
    fireEvent.click(section(0).getByRole('button', { name: 'Start reports' }));
    await waitFor(() =>
      expect(apiMock.reportSubscribe).toHaveBeenCalledWith('mgr', 's1'),
    );
    expect(await screen.findByRole('button', { name: 'Stop reports' })).toBeTruthy();
    expect(box.getByText('Boss')).toBeTruthy();
  });

  it('toggles managed readonly via the readonly endpoint and reflects success', async () => {
    mockManagedByParent({ readonlySession: false });

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    const readonly = await waitFor(() =>
      section(0).getByRole('button', { name: 'Readonly' }),
    );
    expect(readonly.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(readonly);
    await waitFor(() =>
      expect(apiMock.setSessionReadonly).toHaveBeenCalledWith('mgr', 's1', true),
    );
    // The readonly path must not touch management or report subscriptions.
    expect(apiMock.unclaimSession).not.toHaveBeenCalled();
    expect(apiMock.reportUnsubscribe).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(readonly.getAttribute('aria-pressed')).toBe('true'),
    );
    expect(section(0).getByText('Boss')).toBeTruthy();
  });

  it('does not fake readonly success when the readonly call fails', async () => {
    mockManagedByParent({ readonlySession: false });
    apiMock.setSessionReadonly.mockRejectedValueOnce(new Error('readonly failed'));

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    const readonly = await waitFor(() =>
      section(0).getByRole('button', { name: 'Readonly' }),
    );
    fireEvent.click(readonly);
    await waitFor(() =>
      expect(apiMock.setSessionReadonly).toHaveBeenCalledWith('mgr', 's1', true),
    );
    // No optimistic local update on failure: the toggle stays off and the
    // manage link is untouched.
    expect(readonly.getAttribute('aria-pressed')).toBe('false');
    expect(section(0).getByText('Boss')).toBeTruthy();
    expect(apiMock.unclaimSession).not.toHaveBeenCalled();
  });

  it('hides managed-by actions when the session is unmanaged', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Solo', { managed: [], reportSubscriptions: [] }),
    );
    useSessionStore.setState({
      sessions: [mk('s1', 'Solo')],
      currentSessionId: 's1',
      loadSessions: vi.fn(async () => {}),
    });

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    const box = section(0);
    expect(await screen.findByText('Unmanaged')).toBeTruthy();
    expect(box.queryByRole('button', { name: 'Unmanage' })).toBeNull();
    expect(box.queryByRole('button', { name: 'Stop reports' })).toBeNull();
    expect(box.queryByRole('button', { name: 'Readonly' })).toBeNull();
  });

  it('patches a single pan_access flag without touching the others', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Child', {
        managed: [],
        reportSubscriptions: [],
        panAccess: {
          restrictToManaged: false,
          canClaimUnmanaged: true,
          autoClaimCreated: false,
        },
      }),
    );
    apiMock.patchSession.mockResolvedValue(
      mk('s1', 'Child', {
        panAccess: {
          restrictToManaged: true,
          canClaimUnmanaged: true,
          autoClaimCreated: false,
        },
      }),
    );

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    openTab('Access');
    const restrict = await screen.findByRole('switch', {
      name: 'Restrict to managed',
    });
    expect(restrict.getAttribute('aria-checked')).toBe('false');
    // Pre-existing flags come from the fetched detail session.
    expect(
      screen
        .getByRole('switch', { name: 'Can claim unmanaged' })
        .getAttribute('aria-checked'),
    ).toBe('true');

    fireEvent.click(restrict);
    await waitFor(() =>
      expect(apiMock.patchSession).toHaveBeenCalledWith('s1', {
        panAccess: { restrictToManaged: true },
      }),
    );
    await waitFor(() =>
      expect(restrict.getAttribute('aria-checked')).toBe('true'),
    );
    expect(
      screen
        .getByRole('switch', { name: 'Can claim unmanaged' })
        .getAttribute('aria-checked'),
    ).toBe('true');
  });

  it('saves the enabled MCP server names via patchSession', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Child', { managed: [], reportSubscriptions: [], mcpServers: [] }),
    );
    apiMock.fetchMcpServers.mockResolvedValue([
      { name: 'pan', command: 'node pan.js' },
      { name: 'git', command: 'node git.js' },
    ]);
    apiMock.patchSession.mockImplementation(async (_id, body) =>
      mk('s1', 'Child', { mcpServers: (body as { mcpServers?: string[] }).mcpServers }),
    );

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    // Wait for the catalog to render, then toggle "pan" on.
    openTab('MCP and Plugins');
    const panLabel = await screen.findByText('pan');
    const panInput = panLabel.closest('label')!.querySelector('input')!;
    expect((panInput as HTMLInputElement).checked).toBe(false);

    fireEvent.click(panInput);
    await waitFor(() =>
      expect(apiMock.patchSession).toHaveBeenCalledWith('s1', {
        mcpServers: ['pan'],
      }),
    );
    // Optimistic + server echo: the checkbox reflects enabled state.
    await waitFor(() =>
      expect((panInput as HTMLInputElement).checked).toBe(true),
    );
  });

  it('shows readonly beside Subscribe and persists managed-session toggles', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Boss', { managed: ['child'], reportSubscriptions: [] }),
    );
    useSessionStore.setState({
      sessions: [mk('s1', 'Boss'), mk('child', 'Child', { managedBy: 's1' })],
    });

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    const readonly = await screen.findByRole('button', { name: 'Readonly' });
    expect(readonly.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(readonly);
    await waitFor(() =>
      expect(apiMock.setSessionReadonly).toHaveBeenCalledWith('s1', 'child', true),
    );
    expect(await screen.findByRole('button', { name: 'Readonly' })).toBeTruthy();
  });

  it('keeps candidate rows readable with horizontal scroll and intact action buttons', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Boss', { managed: ['child'], reportSubscriptions: ['child'] }),
    );
    useSessionStore.setState({
      sessions: [
        mk('s1', 'Boss'),
        mk('child', 'A very long child session name that must stay readable', {
          managedBy: 's1',
          adapter: 'kimi',
        }),
      ],
    });

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    // The candidate row renders the full name and keeps it in the title so a
    // visually truncated label never loses the complete value.
    const nameEl = await screen.findByText(
      'A very long child session name that must stay readable',
    );
    expect(nameEl.getAttribute('title')).toBe(
      'A very long child session name that must stay readable',
    );

    // Name column keeps a min-width so flex can't crush it to zero next to the
    // shrink-0 action buttons on narrow screens.
    const nameCol = nameEl.parentElement!;
    expect(nameCol.className).toContain('min-w-32');
    expect(nameCol.className).not.toContain('min-w-0');

    // Rows stay on a single line and the list container scrolls horizontally
    // instead of collapsing row content (vertical scrolling stays intact).
    const row = nameCol.parentElement!;
    expect(row.className).toContain('whitespace-nowrap');
    const list = row.parentElement!;
    expect(list.className).toContain('overflow-x-auto');
    expect(list.className).toContain('overflow-y-auto');

    // All three action buttons remain present on the row with their labels.
    const labels = within(row)
      .getAllByRole('button')
      .map((b) => b.textContent)
      .join('|');
    expect(labels).toContain('Managed');
    expect(labels).toContain('Subscribed');
    expect(labels).toContain('Readonly');
  });

  it('disables MCP selection when the template locks it (mcpLocked)', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Child', {
        managed: [],
        reportSubscriptions: [],
        mcpServers: ['pan'],
        mcpLocked: true,
      }),
    );
    apiMock.fetchMcpServers.mockResolvedValue([
      { name: 'pan', command: 'node pan.js' },
    ]);

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    // An unspecified lock reason remains fully locked and has no selectors.
    openTab('MCP and Plugins');
    await screen.findByText(/MCP is locked by the session template/);
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('keeps the MCP catalog visible for an always-on template', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Child', {
        managed: [],
        reportSubscriptions: [],
        mcpServers: ['pan'],
        mcpLocked: true,
        mcpLockReason: 'always',
      }),
    );
    apiMock.fetchMcpServers.mockResolvedValue([
      { name: 'pan', command: 'node pan.js' },
      { name: 'git', command: 'node git.js' },
    ]);
    apiMock.patchSession.mockImplementation(async (_id, body) =>
      mk('s1', 'Child', { mcpServers: (body as { mcpServers?: string[] }).mcpServers }),
    );

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    openTab('MCP and Plugins');
    const panLabel = await screen.findByText('pan');
    const gitLabel = await screen.findByText('git');
    const panInput = panLabel.closest('label')!.querySelector('input') as HTMLInputElement;
    const gitInput = gitLabel.closest('label')!.querySelector('input') as HTMLInputElement;
    expect(panInput.checked).toBe(true);
    expect(panInput.disabled).toBe(true);
    expect(gitInput.disabled).toBe(false);

    fireEvent.click(gitInput);
    await waitFor(() =>
      expect(apiMock.patchSession).toHaveBeenCalledWith('s1', {
        mcpServers: ['pan', 'git'],
        forceMcp: true,
      }),
    );
  });

  it('does not let an older detail response replace the newly opened Session', async () => {
    const first = mk('s1', 'First', { managed: [] });
    const second = mk('s2', 'Second', { managed: [] });
    let resolveFirst: (value: Session) => void = () => {};
    let resolveSecond: (value: Session) => void = () => {};
    apiMock.fetchSession.mockImplementation((id: string) => new Promise((resolve) => {
      if (id === first.id) resolveFirst = resolve;
      else resolveSecond = resolve;
    }));
    useSessionStore.setState({ sessions: [first, second], currentSessionId: first.id });

    const { rerender } = render(<ManageModal open onClose={() => {}} sessionId={first.id} />);
    rerender(<ManageModal open onClose={() => {}} sessionId={second.id} />);

    resolveFirst({ ...first, name: 'Stale First' });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByText(/Stale First manages/)).toBeNull();

    resolveSecond({ ...second, name: 'Fresh Second' });
    expect(await screen.findByText(/Fresh Second manages/)).toBeTruthy();
    expect(screen.queryByText(/Stale First manages/)).toBeNull();
  });

  it('renders the four Manage tabs and switches their content', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Child', { managed: [], reportSubscriptions: [], mcpServers: [] }),
    );
    apiMock.fetchMcpServers.mockResolvedValue([{ name: 'pan', command: 'node pan.js' }]);
    apiMock.fetchWorkspaces.mockResolvedValue([mkWorkspace('w-a', 'Alpha')]);

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);

    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Relationship',
      'Workspaces',
      'Access',
      'MCP and Plugins',
    ]);
    expect(screen.getByRole('tab', { name: 'Relationship' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe('manage-tab-relationship');

    // Relationship holds both management-relationship sections.
    expect(section(0).getByText('Managed by')).toBeTruthy();
    expect(section(1).getByText('Manages / 管理谁')).toBeTruthy();

    openTab('Workspaces');
    expect(section(0).getByText('Workspaces')).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'Alpha' })).toBeTruthy();
    expect(screen.getByRole('tabpanel').getAttribute('aria-labelledby')).toBe('manage-tab-workspaces');

    openTab('Access');
    expect(section(0).getByText('Pan Access')).toBeTruthy();
    expect(screen.getByRole('switch', { name: 'Restrict to managed' })).toBeTruthy();
    // The inaccurate Chinese label is gone; the tab stays "Pan Access".
    expect(screen.queryByText(/MCP 权限/)).toBeNull();

    openTab('MCP and Plugins');
    expect(section(0).getByText(/MCP Server/)).toBeTruthy();
    expect(await screen.findByText('pan')).toBeTruthy();
  });

  it('shows the Workspaces loading and empty states', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Solo', { managed: [], reportSubscriptions: [] }),
    );
    useSessionStore.setState({
      sessions: [mk('s1', 'Solo')],
      currentSessionId: 's1',
      loadSessions: vi.fn(async () => {}),
    });
    let resolveWorkspaces: (value: Workspace[]) => void = () => {};
    apiMock.fetchWorkspaces.mockImplementation(() => new Promise<Workspace[]>((resolve) => {
      resolveWorkspaces = resolve;
    }));

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);
    openTab('Workspaces');
    expect(await screen.findByText('Loading workspaces…')).toBeTruthy();

    await act(async () => {
      resolveWorkspaces([]);
    });

    expect(await screen.findByText('No workspaces yet')).toBeTruthy();
    expect(screen.getByText(/Stored on this session/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Ungrouped' })).toBeTruthy();
  });

  it('shows a Workspaces failure with a retry that recovers', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Solo', { managed: [], reportSubscriptions: [] }),
    );
    useSessionStore.setState({
      sessions: [mk('s1', 'Solo')],
      currentSessionId: 's1',
      loadSessions: vi.fn(async () => {}),
    });
    apiMock.fetchWorkspaces
      .mockRejectedValueOnce(new Error('catalog down'))
      .mockResolvedValueOnce([mkWorkspace('w-a', 'Alpha')]);

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);
    openTab('Workspaces');

    expect(await screen.findByText(/Workspaces unavailable: catalog down/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Alpha' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

    expect(await screen.findByRole('button', { name: 'Alpha' })).toBeTruthy();
    expect(screen.queryByText(/Workspaces unavailable/)).toBeNull();
  });

  it('shows a managed child its inherited workspace and moves it after confirmation', async () => {
    seedManagedChild();
    const confirmation = new Promise<void>((resolve) => {
      window.addEventListener('pan:confirm-workspace-manager-change', ((event: Event) => {
        const detail = (event as CustomEvent<{ changeType: string; resolve: (accepted: boolean) => void }>).detail;
        expect(detail.changeType).toBe('detach');
        detail.resolve(true);
        resolve();
      }) as EventListener, { once: true });
    });

    render(<ManageModal open onClose={() => {}} sessionId="child" />);
    openTab('Workspaces');

    // Effective ownership comes from the manager chain, not the child's own row.
    const summary = (await screen.findByText(/Inherited from "Boss"/)).parentElement!;
    expect(summary.textContent).toContain('Alpha');
    const alpha = screen.getByRole('button', { name: 'Alpha' }) as HTMLButtonElement;
    expect(alpha.getAttribute('aria-pressed')).toBe('true');
    expect(alpha.disabled).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Beta' }));
    await confirmation;

    await waitFor(() => expect(apiMock.unclaimSession).toHaveBeenCalledWith('mgr', 'child'));
    await waitFor(() => expect(apiMock.setSessionWorkspaces).toHaveBeenCalledWith('child', ['w-b']));
  });

  it('declines a cross-workspace move without sending any relationship or workspace request', async () => {
    seedManagedChild();
    const declined = new Promise<void>((resolve) => {
      window.addEventListener('pan:confirm-workspace-manager-change', ((event: Event) => {
        const detail = (event as CustomEvent<{ resolve: (accepted: boolean) => void }>).detail;
        detail.resolve(false);
        resolve();
      }) as EventListener, { once: true });
    });

    render(<ManageModal open onClose={() => {}} sessionId="child" />);
    openTab('Workspaces');
    fireEvent.click(await screen.findByRole('button', { name: 'Beta' }));
    await declined;
    // Let the declined call settle so a stray toast would have been queued.
    await act(async () => {});

    expect(apiMock.unclaimSession).not.toHaveBeenCalled();
    expect(apiMock.setSessionWorkspaces).not.toHaveBeenCalled();
    // Still inherited, and a declined move must not raise a move toast.
    expect(screen.getByText(/Inherited from "Boss"/).parentElement!.textContent).toContain('Alpha');
    expect(useUIStore.getState().toastQueue).toEqual([]);
  });

  it('moves across workspaces without prompting when the confirmation setting is off', async () => {
    seedManagedChild({ confirmation: false });
    const confirmations: string[] = [];
    const listener = ((event: Event) => {
      confirmations.push((event as CustomEvent<{ changeType: string }>).detail.changeType);
    }) as EventListener;
    window.addEventListener('pan:confirm-workspace-manager-change', listener);

    try {
      render(<ManageModal open onClose={() => {}} sessionId="child" />);
      openTab('Workspaces');
      fireEvent.click(await screen.findByRole('button', { name: 'Beta' }));

      await waitFor(() => expect(apiMock.setSessionWorkspaces).toHaveBeenCalledWith('child', ['w-b']));
      expect(apiMock.unclaimSession).toHaveBeenCalledWith('mgr', 'child');
      expect(confirmations).toEqual([]);
    } finally {
      window.removeEventListener('pan:confirm-workspace-manager-change', listener);
    }
  });

  it('writes only the root membership for an unmanaged session', async () => {
    useSessionStore.setState({
      sessions: [mk('root', 'Root', { workspaceIds: ['w-a'] })],
      currentSessionId: 'root',
      loadSessions: vi.fn(async () => {}),
    });
    useWorkspaceStore.setState({
      workspaces: [mkWorkspace('w-a', 'Alpha'), mkWorkspace('w-b', 'Beta')],
      loaded: true,
      loading: false,
      error: null,
    });
    apiMock.fetchSession.mockResolvedValue(
      mk('root', 'Root', { managed: [], reportSubscriptions: [] }),
    );

    render(<ManageModal open onClose={() => {}} sessionId="root" />);
    openTab('Workspaces');
    expect(screen.getByText(/Stored on this session/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Beta' }));

    await waitFor(() => expect(apiMock.setSessionWorkspaces).toHaveBeenCalledWith('root', ['w-b']));
    expect(apiMock.unclaimSession).not.toHaveBeenCalled();
    expect(apiMock.claimSession).not.toHaveBeenCalled();
  });

  it('keeps the desktop modal close paths working', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Child', { managed: [], reportSubscriptions: [] }),
    );
    const onClose = vi.fn();

    render(<ManageModal open onClose={onClose} sessionId="s1" />);
    await screen.findByText('Managed by');

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('supports keyboard navigation across the Manage tabs', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Child', { managed: [], reportSubscriptions: [] }),
    );

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);
    const tablist = screen.getByRole('tablist', { name: 'Manage sections' });
    const selected = () =>
      screen.getAllByRole('tab').filter((tab) => tab.getAttribute('aria-selected') === 'true')
        .map((tab) => tab.textContent);

    fireEvent.keyDown(tablist, { key: 'ArrowRight' });
    expect(selected()).toEqual(['Workspaces']);

    fireEvent.keyDown(tablist, { key: 'End' });
    expect(selected()).toEqual(['MCP and Plugins']);

    // ArrowRight from the last tab wraps to the first one.
    fireEvent.keyDown(tablist, { key: 'ArrowRight' });
    expect(selected()).toEqual(['Relationship']);

    fireEvent.keyDown(tablist, { key: 'Home' });
    expect(selected()).toEqual(['Relationship']);

    // ArrowLeft from the first tab wraps to the last one.
    fireEvent.keyDown(tablist, { key: 'ArrowLeft' });
    expect(selected()).toEqual(['MCP and Plugins']);
  });

  it('resets to the Relationship tab when the panel is reopened', async () => {
    apiMock.fetchSession.mockResolvedValue(
      mk('s1', 'Child', { managed: [], reportSubscriptions: [] }),
    );

    const { rerender } = render(<ManageModal open onClose={() => {}} sessionId="s1" />);
    openTab('Access');
    expect(screen.getByRole('tab', { name: 'Access' }).getAttribute('aria-selected')).toBe('true');

    rerender(<ManageModal open={false} onClose={() => {}} sessionId="s1" />);
    rerender(<ManageModal open onClose={() => {}} sessionId="s1" />);

    expect(screen.getByRole('tab', { name: 'Relationship' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tab', { name: 'Access' }).getAttribute('aria-selected')).toBe('false');
  });

  it('fails closed without crashing when the manager is missing from the list', async () => {
    useSessionStore.setState({
      sessions: [mk('orphan', 'Orphan', { managedBy: 'ghost', workspaceIds: ['w-a'] })],
      currentSessionId: 'orphan',
      loadSessions: vi.fn(async () => {}),
    });
    useWorkspaceStore.setState({
      workspaces: [mkWorkspace('w-a', 'Alpha')],
      loaded: true,
      loading: false,
      error: null,
    });
    apiMock.fetchSession.mockResolvedValue(
      mk('orphan', 'Orphan', { managedBy: 'ghost', managed: [], reportSubscriptions: [] }),
    );

    render(<ManageModal open onClose={() => {}} sessionId="orphan" />);
    openTab('Workspaces');

    // Broken chain → no effective membership; the orphan's own row is ignored.
    const summary = (await screen.findByText(/Inherited from "ghost"/)).parentElement!;
    expect(summary.textContent).toContain('Ungrouped');
    expect(screen.getByRole('button', { name: 'Ungrouped' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: 'Alpha' }).getAttribute('aria-pressed')).toBe('false');
  });

  it('keeps cached metadata visible and offers retry after a detail failure', async () => {
    apiMock.fetchSession
      .mockRejectedValueOnce(new Error('metadata timeout'))
      .mockResolvedValueOnce(mk('s1', 'Fresh after retry', { managed: [] }));
    useSessionStore.setState({ sessions: [mk('s1', 'Child')] });

    render(<ManageModal open onClose={() => {}} sessionId="s1" />);
    expect(await screen.findByText('error')).toBeTruthy();
    expect(screen.getByText(/metadata timeout/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('updated')).toBeTruthy();
    expect(screen.getByText(/Fresh after retry manages/)).toBeTruthy();
  });
});
