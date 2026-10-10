// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ReimportConfirmationModal } from './ReimportConfirmationModal';
import { SessionMenu } from './SessionMenu';
import { ReimportError, type ReimportConflict } from '@/services/api';
import { useReimportConfirmationStore } from '@/stores/reimportConfirmationStore';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import type { Session } from '@/types';

const conflict: ReimportConflict = {
  sessionId: 'ses-confirm', historyRevision: 4, count: 1, omittedCount: 0,
  confirmationToken: 'first-token', stale: false,
  items: [{ position: 12, source: 'agent', reason: 'missing_or_changed', preview: 'Missing message preview' }],
};
const session: Session = { id: 'ses-confirm', name: 'Confirm target', cliSessionId: 'native-confirm',
  adapter: 'codex', alwaysThinkingEnabled: false, effort: '', history: [] };
const originalReimport = useSessionStore.getState().reimport;
const originalWorkspace = useUIStore.getState().activeWorkspaceId;

afterEach(() => {
  cleanup();
  useReimportConfirmationStore.getState().close();
  useSessionStore.setState({ reimport: originalReimport });
  useUIStore.setState({ activeWorkspaceId: originalWorkspace });
});

function Harness() {
  const [menu, setMenu] = useState(true);
  return <>{menu && <SessionMenu session={session} position={{ x: 1, y: 1 }} onClose={() => setMenu(false)} />}
    <ReimportConfirmationModal /></>;
}

describe('reimport confirmation interaction', () => {
  it('survives menu unmount, shows summary and cancels without a force request', async () => {
    const reimport = vi.fn().mockRejectedValueOnce(new ReimportError('conflict', 'reimport_origin_conflict', conflict));
    useSessionStore.setState({ reimport });
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Reimport' }));
    await screen.findByRole('dialog');
    expect(screen.getByText('位置 12 · agent · 原生历史中缺失或正文已变化')).toBeTruthy();
    expect(screen.getByText('Missing message preview')).toBeTruthy();
    expect(screen.getByText(/无法匹配的 Pan 消息/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(reimport).toHaveBeenCalledTimes(1);
  });

  it('confirms only the displayed token and preserves the Workspace captured at opening', async () => {
    const reimport = vi.fn().mockRejectedValueOnce(new ReimportError('conflict', 'reimport_origin_conflict', conflict))
      .mockResolvedValueOnce(undefined);
    useSessionStore.setState({ reimport });
    useUIStore.setState({ activeWorkspaceId: 'ws-original' });
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Reimport' }));
    await screen.findByRole('dialog');
    useUIStore.setState({ activeWorkspaceId: 'ws-later' });
    fireEvent.click(screen.getByRole('button', { name: '确认强行覆盖' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(reimport).toHaveBeenLastCalledWith('ses-confirm', 'ws-original', 'first-token');
  });

  it('requires a second click after a stale response updates the summary and token', async () => {
    const newer = { ...conflict, confirmationToken: 'new-token', stale: true, count: 2,
      items: [...conflict.items, { position: 13, source: 'user', reason: 'ambiguous_match', preview: 'New conflict' }] };
    const reimport = vi.fn().mockRejectedValueOnce(new ReimportError('conflict', 'reimport_origin_conflict', conflict))
      .mockRejectedValueOnce(new ReimportError('stale', 'reimport_stale', newer)).mockResolvedValueOnce(undefined);
    useSessionStore.setState({ reimport });
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Reimport' }));
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: '确认强行覆盖' }));
    await screen.findByText('New conflict');
    expect(reimport).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('alert').textContent).toContain('再次确认');
    fireEvent.click(screen.getByRole('button', { name: '确认强行覆盖' }));
    await waitFor(() => expect(reimport).toHaveBeenCalledTimes(3));
    expect(reimport.mock.calls[2]?.[2]).toBe('new-token');
  });

  it('invalidates the old confirmation on a ticket conflict and explicitly rechecks', async () => {
    const reimport = vi.fn().mockRejectedValueOnce(new ReimportError('conflict', 'reimport_origin_conflict', conflict))
      .mockRejectedValueOnce(new ReimportError('changed while saving', 'reimport_stale'))
      .mockRejectedValueOnce(new ReimportError('conflict', 'reimport_origin_conflict', { ...conflict, confirmationToken: 'fresh' }));
    useSessionStore.setState({ reimport });
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Reimport' }));
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: '确认强行覆盖' }));
    fireEvent.click(await screen.findByRole('button', { name: '重新检查' }));
    await screen.findByRole('button', { name: '确认强行覆盖' });
    expect(reimport.mock.calls[2]?.[2]).toBeUndefined();
    expect(reimport).toHaveBeenCalledTimes(3);
  });

  it('keeps failure visible and prevents dismissal or duplicate requests while saving', async () => {
    let fail!: (error: Error) => void;
    const waiting = new Promise<void>((_, reject) => { fail = reject; });
    const reimport = vi.fn().mockReturnValueOnce(waiting);
    useSessionStore.setState({ reimport });
    useReimportConfirmationStore.getState().open({ sessionId: session.id, sessionName: session.name!, workspaceId: undefined, conflict });
    render(<ReimportConfirmationModal />);
    fireEvent.click(screen.getByRole('button', { name: '确认强行覆盖' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect((screen.getByRole('button', { name: '取消' }) as HTMLButtonElement).disabled).toBe(true);
    fail(new Error('disk save failed'));
    expect((await screen.findByRole('alert')).textContent).toContain('disk save failed');
    expect(reimport).toHaveBeenCalledTimes(1);
  });
});
