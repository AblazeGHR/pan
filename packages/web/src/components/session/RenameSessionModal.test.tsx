// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RenameSessionModal } from './RenameSessionModal';
import { useSessionStore } from '@/stores/sessionStore';
import type { Session } from '@/types';

const session: Session = { id: 'rename-session', name: 'Original name', alwaysThinkingEnabled: false, effort: '', history: [] };
const otherSession: Session = { id: 'other-session', name: 'Other name', alwaysThinkingEnabled: false, effort: '', history: [] };

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

/** Replicates the production wiring in Sidebar.tsx:808-811: the modal stays
 *  mounted and receives the live store object for the open session id. */
function SidebarHost({ sessionId }: { sessionId: string | null }) {
  const sessions = useSessionStore((state) => state.sessions);
  return (
    <RenameSessionModal
      session={sessionId ? sessions.find((s) => s.id === sessionId) ?? null : null}
      onClose={() => {}}
    />
  );
}

describe('RenameSessionModal', () => {
  it('prefills and selects the original name, then submits on Enter', async () => {
    const rename = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ rename });
    const onClose = vi.fn();
    render(<RenameSessionModal session={session} onClose={onClose} />);
    const input = await screen.findByRole('textbox', { name: 'Session name' });
    await waitFor(() => expect((input as HTMLInputElement).value).toBe(session.name));
    expect(document.activeElement).toBe(input);
    expect((input as HTMLInputElement).selectionStart).toBe(0);
    expect((input as HTMLInputElement).selectionEnd).toBe(session.name.length);
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.submit(input.closest('form')!);
    await waitFor(() => expect(rename).toHaveBeenCalledWith(session.id, session.name));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes on Escape and rejects an empty name', async () => {
    const rename = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ rename });
    const onClose = vi.fn();
    render(<RenameSessionModal session={session} onClose={onClose} />);
    const input = await screen.findByRole('textbox', { name: 'Session name' });
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(rename).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('keeps the typed draft when a store refresh replaces the session object', async () => {
    // A refresh while the dialog is open (WS stream preview, session.updated,
    // loadSessions merge) hands Sidebar a NEW session object with the same id.
    // The draft must survive it, and the draft — not the stale server name —
    // is what gets submitted.
    const rename = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ rename, sessions: [session] });
    render(<SidebarHost sessionId="rename-session" />);
    const input = await screen.findByRole('textbox', { name: 'Session name' });
    fireEvent.change(input, { target: { value: 'Typed draft' } });
    expect((input as HTMLInputElement).value).toBe('Typed draft');
    act(() => {
      useSessionStore.getState().updateSession('rename-session', { lastMessage: 'stream preview' });
    });
    expect((input as HTMLInputElement).value).toBe('Typed draft');
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.submit(input.closest('form')!);
    await waitFor(() => expect(rename).toHaveBeenCalledWith('rename-session', 'Typed draft'));
  });

  it('keeps the submit guard armed while a rename request is in flight across a refresh', async () => {
    let resolveRename: (() => void) | undefined;
    const rename = vi.fn().mockImplementation(
      () => new Promise<void>((resolve) => { resolveRename = resolve; }),
    );
    useSessionStore.setState({ rename, sessions: [session] });
    render(<SidebarHost sessionId="rename-session" />);
    const input = await screen.findByRole('textbox', { name: 'Session name' });
    fireEvent.change(input, { target: { value: 'Typed draft' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.submit(input.closest('form')!);
    await waitFor(() => expect(rename).toHaveBeenCalledTimes(1));
    expect((input as HTMLInputElement).disabled).toBe(true);
    act(() => {
      useSessionStore.getState().updateSession('rename-session', { lastMessage: 'stream preview' });
    });
    expect((input as HTMLInputElement).disabled).toBe(true);
    await act(async () => { resolveRename?.(); });
  });

  it('reseeds the draft and re-selects when switching to another session without closing', async () => {
    const rename = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ rename, sessions: [session, otherSession] });
    const view = render(<SidebarHost sessionId="rename-session" />);
    const input = await screen.findByRole('textbox', { name: 'Session name' });
    fireEvent.change(input, { target: { value: 'Typed draft' } });
    view.rerender(<SidebarHost sessionId="other-session" />);
    // Poll the whole { value, selection } state: the baseline re-applies the
    // selection on a later animation frame, so it is not stable on the first
    // read (sometimes it collapses to a caret at the end).
    await waitFor(
      () => {
        const el = input as HTMLInputElement;
        expect({ value: el.value, start: el.selectionStart, end: el.selectionEnd })
          .toEqual({ value: otherSession.name, start: 0, end: otherSession.name.length });
      },
      { timeout: 5000 },
    );
  });

  it('clears the draft when closed so it cannot leak into the next session', async () => {
    const rename = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ rename, sessions: [session, otherSession] });
    const view = render(<SidebarHost sessionId="rename-session" />);
    const input = await screen.findByRole('textbox', { name: 'Session name' });
    fireEvent.change(input, { target: { value: 'Typed draft' } });
    view.rerender(<SidebarHost sessionId={null} />);
    view.rerender(<SidebarHost sessionId="other-session" />);
    const reopened = await screen.findByRole('textbox', { name: 'Session name' });
    await waitFor(
      () => {
        const el = reopened as HTMLInputElement;
        expect({ value: el.value, start: el.selectionStart, end: el.selectionEnd })
          .toEqual({ value: otherSession.name, start: 0, end: otherSession.name.length });
      },
      { timeout: 5000 },
    );
  });
});
