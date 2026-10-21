// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { AgentQueueItem } from '@/types';

const clipboard = vi.hoisted(() => ({ copyText: vi.fn() }));
vi.mock('@/utils/clipboard', () => clipboard);

const navigation = vi.hoisted(() => ({ openSessionFromNotification: vi.fn() }));
vi.mock('@/utils/session-deeplink', () => ({
  isValidSessionRef: (value: string | null | undefined) =>
    typeof value === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(value),
  openSessionFromNotification: navigation.openSessionFromNotification,
}));

import { QueueSourceSession } from './QueueSourceSession';
import { hasSessionOrigin, storedSourceSessionId } from '@/utils/queueSourceSession';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';

const SOURCE_ID = 'ses_0123456789abcdef';

function session(id: string, name: string) {
  return {
    id,
    name,
    alwaysThinkingEnabled: false,
    effort: '',
    history: [],
  };
}

function queueItem(overrides: Partial<AgentQueueItem> = {}): AgentQueueItem {
  return {
    id: 'q-1',
    queueItemId: 'q-1',
    kind: 'task',
    text: 'queued message body',
    createdAt: 0,
    source: 'agent',
    meta: { dispatchState: 'queued', revision: 1, sourceSessionId: SOURCE_ID },
    ...overrides,
  };
}

beforeEach(() => {
  clipboard.copyText.mockReset();
  clipboard.copyText.mockResolvedValue(undefined);
  navigation.openSessionFromNotification.mockReset();
  navigation.openSessionFromNotification.mockResolvedValue(true);
  useUIStore.setState({ toastQueue: [] });
  useSessionStore.setState({
    currentSessionId: 'ses_target',
    sessions: [session(SOURCE_ID, 'Parent Orchestrator')],
    currentMessages: [],
  });
});

afterEach(() => cleanup());

describe('QueueSourceSession identity rules', () => {
  it('treats agent messages and reports as having a session origin and nothing else', () => {
    expect(hasSessionOrigin(queueItem())).toBe(true);
    expect(hasSessionOrigin(queueItem({ kind: 'report', source: 'report' }))).toBe(true);
    expect(hasSessionOrigin(queueItem({ source: 'user', meta: { sourceSessionId: SOURCE_ID } }))).toBe(false);
    expect(hasSessionOrigin(queueItem({ source: 'qq', kind: 'qq', meta: { sourceSessionId: SOURCE_ID } }))).toBe(false);
  });

  it('reads the origin only from the structured meta field', () => {
    expect(storedSourceSessionId(queueItem())).toBe(SOURCE_ID);
    expect(storedSourceSessionId(queueItem({ meta: { dispatchState: 'queued' } }))).toBeNull();
    expect(storedSourceSessionId(queueItem({ meta: { sourceSessionId: '   ' } }))).toBeNull();
    // A body that merely looks like a Pan envelope is not an identity source.
    expect(storedSourceSessionId(queueItem({
      text: '////by agent : ses_fedcba9876543210 | task',
      meta: { dispatchState: 'queued' },
    }))).toBeNull();
  });

  it('renders nothing for non-agent sources even when an id is present', () => {
    const { container } = render(
      <QueueSourceSession item={queueItem({ source: 'user', meta: { sourceSessionId: SOURCE_ID } })} />,
    );
    expect(container.firstChild).toBeNull();
    expect(screen.queryByText('Source session')).toBeNull();
  });

  it('renders nothing for a report without a stored origin', () => {
    const { container } = render(
      <QueueSourceSession item={queueItem({ kind: 'report', source: 'report', meta: { dispatchState: 'queued' } })} />,
    );
    expect(container.firstChild).toBeNull();
  });
});

describe('QueueSourceSession copy feedback', () => {
  it('shows the session name and id and copies each value separately', async () => {
    render(<QueueSourceSession item={queueItem()} />);

    expect(screen.getByText('Parent Orchestrator')).toBeTruthy();
    expect(screen.getByText(SOURCE_ID)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Copy source session name' }));
    await waitFor(() => expect(clipboard.copyText).toHaveBeenCalledWith('Parent Orchestrator'));
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Copied source session name'));

    fireEvent.click(screen.getByRole('button', { name: 'Copy source session ID' }));
    await waitFor(() => expect(clipboard.copyText).toHaveBeenLastCalledWith(SOURCE_ID));
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe('Copied source session ID'));
  });

  it('lets the name and the id themselves be clicked to copy', async () => {
    render(<QueueSourceSession item={queueItem()} />);

    fireEvent.click(screen.getByText('Parent Orchestrator'));
    await waitFor(() => expect(clipboard.copyText).toHaveBeenCalledWith('Parent Orchestrator'));

    fireEvent.click(screen.getByText(SOURCE_ID));
    await waitFor(() => expect(clipboard.copyText).toHaveBeenLastCalledWith(SOURCE_ID));
  });

  it('reports a clipboard failure in English instead of claiming success', async () => {
    clipboard.copyText.mockRejectedValueOnce(new Error('denied'));
    render(<QueueSourceSession item={queueItem()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy source session ID' }));
    await waitFor(() => expect(useUIStore.getState().toastQueue.some((toast) => toast.message === 'Copy failed')).toBe(true));
    expect(screen.getByRole('status').textContent).toBe('');
  });
});

describe('QueueSourceSession degraded origins', () => {
  it('keeps the known id copyable when the session is no longer in the list', async () => {
    useSessionStore.setState({ sessions: [] });
    render(<QueueSourceSession item={queueItem()} />);

    expect(screen.getByText('Unavailable')).toBeTruthy();
    expect(screen.getByText(/not in the loaded session list/)).toBeTruthy();
    expect(screen.getByText(SOURCE_ID)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Copy source session ID' }));
    await waitFor(() => expect(clipboard.copyText).toHaveBeenCalledWith(SOURCE_ID));
    expect(screen.getByRole('button', { name: 'Copy source session name' })).toHaveProperty('disabled', true);
  });

  it('says the id was not recorded for an agent message without one, and offers no jump', () => {
    render(<QueueSourceSession item={queueItem({ meta: { dispatchState: 'queued' } })} />);

    expect(screen.getByText('No source session ID was recorded for this message.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Copy source session ID' })).toBeNull();
    expect(screen.queryByText('Open source chat')).toBeNull();
    expect(screen.queryByText('Unavailable')).toBeNull();
  });
});

describe('QueueSourceSession jump', () => {
  it('closes the preview first and reuses the existing session routing', async () => {
    const onClose = vi.fn();
    render(<QueueSourceSession item={queueItem()} onClose={onClose} />);

    fireEvent.click(screen.getByText('Open source chat'));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(navigation.openSessionFromNotification).toHaveBeenCalledTimes(1);
    const [sessionId, messages] = navigation.openSessionFromNotification.mock.calls[0]!;
    expect(sessionId).toBe(SOURCE_ID);
    // The jump's own feedback stays English (no CJK fallbacks).
    expect(messages).toMatchObject({
      missing: expect.any(String),
      listUnavailable: expect.any(String),
      workspaceUnavailable: expect.any(String),
      routeFailed: expect.any(String),
    });
    for (const message of Object.values(messages as Record<string, string>)) {
      expect(/^[\x20-\x7E]+$/.test(message)).toBe(true);
    }
    expect(onClose.mock.invocationCallOrder[0]!).toBeLessThan(
      navigation.openSessionFromNotification.mock.invocationCallOrder[0]!,
    );
  });

  it('does not jump when the recorded id cannot be a session reference', () => {
    const onClose = vi.fn();
    render(
      <QueueSourceSession
        item={queueItem({ meta: { dispatchState: 'queued', sourceSessionId: 'not a valid id!' } })}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByText('Open source chat'));
    expect(onClose).not.toHaveBeenCalled();
    expect(navigation.openSessionFromNotification).not.toHaveBeenCalled();
    expect(useUIStore.getState().toastQueue.some((toast) => toast.message === 'The recorded source session ID cannot be opened.')).toBe(true);
  });
});

describe('QueueSourceSession isolation between previews', () => {
  it('never carries a late copy result over to another message', async () => {
    let resolveCopy: (() => void) | undefined;
    clipboard.copyText.mockImplementationOnce(() => new Promise<void>((resolve) => { resolveCopy = resolve; }));

    const { rerender } = render(<QueueSourceSession item={queueItem({ id: 'q-1' })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy source session ID' }));

    // The user opens a different queued message before the clipboard resolves.
    rerender(<QueueSourceSession item={queueItem({ id: 'q-2', text: 'other body' })} />);
    expect(screen.getByRole('status').textContent).toBe('');

    resolveCopy?.();
    await waitFor(() => expect(clipboard.copyText).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('status').textContent).toBe('');
  });

  it('drops a rejected copy when the user switched to another preview', async () => {
    let rejectCopy: ((error: Error) => void) | undefined;
    clipboard.copyText.mockImplementationOnce(() => new Promise<void>((_resolve, reject) => { rejectCopy = reject; }));

    const { rerender } = render(<QueueSourceSession item={queueItem({ id: 'q-1' })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy source session ID' }));

    rerender(<QueueSourceSession item={queueItem({ id: 'q-2', text: 'other body' })} />);
    rejectCopy?.(new Error('denied'));
    await act(async () => { await Promise.resolve(); });

    expect(useUIStore.getState().toastQueue).toEqual([]);
    expect(screen.getByRole('status').textContent).toBe('');
  });

  it('drops a rejected copy after the preview was closed', async () => {
    let rejectCopy: ((error: Error) => void) | undefined;
    clipboard.copyText.mockImplementationOnce(() => new Promise<void>((_resolve, reject) => { rejectCopy = reject; }));

    const { unmount } = render(<QueueSourceSession item={queueItem({ id: 'q-1' })} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy source session ID' }));

    unmount();
    rejectCopy?.(new Error('denied'));
    await act(async () => { await Promise.resolve(); });

    expect(useUIStore.getState().toastQueue).toEqual([]);
  });
});
