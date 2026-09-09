// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RenameSessionModal } from './RenameSessionModal';
import { useSessionStore } from '@/stores/sessionStore';
import type { Session } from '@/types';

const session: Session = { id: 'rename-session', name: 'Original name', alwaysThinkingEnabled: false, effort: '', history: [] };

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
});
