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
    expect(await screen.findByText('Unmanaged / 未托管')).toBeTruthy();
    expect(screen.queryByTitle(/Break the manage link/)).toBeNull();
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
