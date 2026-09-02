// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
  cleanup,
} from '@testing-library/react';
import { ManageModal } from './ManageModal';
import { useSessionStore } from '@/stores/sessionStore';
import type { McpServerInfo, Session } from '@/types';

const apiMock = vi.hoisted(() => ({
  fetchSession: vi.fn(),
  claimSession: vi.fn(async () => ({ ok: true })),
  unclaimSession: vi.fn(async () => ({ ok: true })),
  reportSubscribe: vi.fn(async () => ({})),
  reportUnsubscribe: vi.fn(async () => ({})),
  fetchMcpServers: vi.fn(async (): Promise<McpServerInfo[]> => []),
  patchSession: vi.fn(),
  setSessionReadonly: vi.fn(async () => ({ ok: true, readonlySession: true })),
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

/** Section order matches the modal: 0 = Managed by, 1 = Manages, 2 = Pan Access, 3 = MCP Server. */
function section(i: number) {
  const el = document.querySelectorAll('section')[i];
  return within(el as HTMLElement);
}

describe('ManageModal', () => {
  // The vitest config has no globals, so RTL auto-cleanup is off — the modal
  // renders through a portal to <body> and would leak into the next test.
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    useSessionStore.setState({
      sessions: [mk('s1', 'Child', { managedBy: 'mgr' }), mk('mgr', 'Boss')],
      currentSessionId: 's1',
      loadSessions: vi.fn(async () => {}),
    });
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
});
