// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RenameSessionModal } from './RenameSessionModal';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
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

  it('discards a late rename result that belongs to a previous edit instance', async () => {
    let resolveRename: (() => void) | undefined;
    const rename = vi.fn().mockImplementation(
      () => new Promise<void>((resolve) => { resolveRename = resolve; }),
    );
    useSessionStore.setState({ rename, sessions: [session] });
    useUIStore.setState({ toastQueue: [] });
    const onClose = vi.fn();
    const view = render(<RenameSessionModal session={session} onClose={onClose} />);
    const input = await screen.findByRole('textbox', { name: 'Session name' });
    fireEvent.change(input, { target: { value: 'A v2' } });
    fireEvent.submit(input.closest('form')!);
    await waitFor(() => expect(rename).toHaveBeenCalledTimes(1));
    // The user closes and reopens the SAME session while the request is still
    // in flight: a new edit instance now owns the dialog. Keying the guard on
    // the session id alone could not tell these two instances apart.
    view.rerender(<RenameSessionModal session={null} onClose={onClose} />);
    view.rerender(<RenameSessionModal session={session} onClose={onClose} />);
    const reopened = await screen.findByRole('textbox', { name: 'Session name' });
    expect((reopened as HTMLInputElement).disabled).toBe(false);
    await act(async () => { resolveRename?.(); });
    // The late success must not close the new dialog or claim its name in a toast.
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox', { name: 'Session name' })).toBeTruthy();
    expect(useUIStore.getState().toastQueue.map((toast) => toast.message))
      .not.toContain('Session renamed');
  });

  it('does not let a stale finaliser release the submit guard of a newer request', async () => {
    const pending: Array<() => void> = [];
    const rename = vi.fn().mockImplementation(
      () => new Promise<void>((resolve) => { pending.push(resolve); }),
    );
    useSessionStore.setState({ rename, sessions: [session, otherSession] });
    const onClose = vi.fn();
    const view = render(<RenameSessionModal session={session} onClose={onClose} />);
    const inputA = await screen.findByRole('textbox', { name: 'Session name' });
    fireEvent.change(inputA, { target: { value: 'A v2' } });
    fireEvent.submit(inputA.closest('form')!);
    await waitFor(() => expect(rename).toHaveBeenCalledTimes(1));
    // Close A mid-flight and start editing B, which has its own request.
    view.rerender(<RenameSessionModal session={null} onClose={onClose} />);
    view.rerender(<RenameSessionModal session={otherSession} onClose={onClose} />);
    const inputB = await screen.findByRole('textbox', { name: 'Session name' });
    fireEvent.change(inputB, { target: { value: 'B v2' } });
    fireEvent.submit(inputB.closest('form')!);
    await waitFor(() => expect(rename).toHaveBeenCalledTimes(2));
    expect(rename).toHaveBeenLastCalledWith('other-session', 'B v2');
    expect((inputB as HTMLInputElement).disabled).toBe(true);
    await act(async () => { pending[0]!(); });
    expect((inputB as HTMLInputElement).disabled).toBe(true);
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => { pending[1]!(); });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('collapses duplicate submits issued in the same event batch', async () => {
    let resolveRename: (() => void) | undefined;
    const rename = vi.fn().mockImplementation(
      () => new Promise<void>((resolve) => { resolveRename = resolve; }),
    );
    useSessionStore.setState({ rename, sessions: [session] });
    render(<RenameSessionModal session={session} onClose={() => {}} />);
    const input = await screen.findByRole('textbox', { name: 'Session name' });
    fireEvent.change(input, { target: { value: 'A v2' } });
    const form = input.closest('form')!;
    // Enter is handled explicitly now, so the same keypress can also reach
    // implicit form submission; the synchronous guard must collapse all paths.
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.submit(form);
    fireEvent.submit(form);
    await waitFor(() => expect(rename).toHaveBeenCalledTimes(1));
    await act(async () => { resolveRename?.(); });
    expect(rename).toHaveBeenCalledTimes(1);
  });

  it('ignores Enter and Escape while an IME composition is active', async () => {
    const rename = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ rename, sessions: [session] });
    const onClose = vi.fn();
    render(<RenameSessionModal session={session} onClose={onClose} />);
    const input = await screen.findByRole('textbox', { name: 'Session name' });
    fireEvent.change(input, { target: { value: 'Composing' } });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
    fireEvent.keyDown(input, { key: 'Escape', isComposing: true });
    expect(rename).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    // Legacy engines report the composition-ending key as keyCode 229 with
    // isComposing already reset; the guard must cover that too. jsdom ignores
    // keyCode in the event init, so define it on a real event.
    const legacyEnter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true });
    Object.defineProperty(legacyEnter, 'keyCode', { get: () => 229 });
    act(() => { input.dispatchEvent(legacyEnter); });
    expect(rename).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    // Once composition ends, Enter submits normally.
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(rename).toHaveBeenCalledWith('rename-session', 'Composing'));
  });
});
