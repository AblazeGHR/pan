// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Session } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { usePlatformStore } from '@/stores/platformStore';
import { MessageBubble } from './MessageBubble';

vi.mock('./MarkdownRenderer', () => ({ MarkdownRenderer: ({ content }: { content: string }) => <span>{content}</span> }));

const message = { role: 'user' as const, content: 'rewind anchor', messageId: 'pan:0123456789abcdef0123456789abcdef', ts: '2026-10-06T12:00:00' };
const originalOpen = useSessionStore.getState().openRewind;

function session(adapter?: string, workerStatus = 'idle') {
  useSessionStore.setState({
    currentSessionId: 'rewind-test',
    sessions: [{ id: 'rewind-test', adapter, workerStatus } as Session],
  });
}

describe('rewind action placement and capability', () => {
  beforeEach(() => session('cbc'));
  afterEach(() => {
    cleanup();
    useSessionStore.setState({ sessions: [], currentSessionId: null, openRewind: originalOpen });
    usePlatformStore.setState({ platform: null, capabilities: null });
  });

  it.each(['codex', 'claude', 'kimi', 'opencode', 'unknown', undefined])('hides rewind for %s', (adapter) => {
    session(adapter);
    render(<MessageBubble message={message} />);
    expect(screen.queryByRole('button', { name: '撤回' })).toBeNull();
    expect(document.querySelector('time')).not.toBeNull();
  });

  it('puts the CBC action immediately before the timestamp in one horizontal row', () => {
    const open = vi.fn();
    useSessionStore.setState({ openRewind: open });
    render(<MessageBubble message={message} />);
    const button = screen.getByRole('button', { name: '撤回' });
    const timestamp = document.querySelector('time')!;
    expect(button.parentElement).toBe(timestamp.parentElement);
    expect(button.nextElementSibling).toBe(timestamp);
    expect(button.parentElement?.className).toContain('flex items-center');
    fireEvent.click(button);
    expect(open).toHaveBeenCalledWith(message);
  });

  it.each(['running', 'queued'])('keeps CBC action disabled while %s', (status) => {
    session('cbc', status);
    render(<MessageBubble message={message} />);
    expect((screen.getByRole('button', { name: '撤回' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('does not show rewind on streaming or assistant rows', () => {
    const view = render(<MessageBubble message={{ ...message, streaming: true }} />);
    expect(screen.queryByRole('button', { name: '撤回' })).toBeNull();
    view.rerender(<MessageBubble message={{ ...message, role: 'assistant' }} />);
    expect(screen.queryByRole('button', { name: '撤回' })).toBeNull();
  });

  it('keeps the entry visible but disabled when the platform denies rewind', () => {
    const open = vi.fn();
    useSessionStore.setState({ openRewind: open });
    usePlatformStore.setState({ platform: 'darwin', capabilities: { rewind: false } });
    render(<MessageBubble message={message} />);
    const button = screen.getByRole('button', { name: '撤回' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toContain('macOS');
    expect(button.title).toContain('暂不支持');
    fireEvent.click(button);
    expect(open).not.toHaveBeenCalled();
  });

  it('stays enabled when the platform probe did not answer', () => {
    usePlatformStore.setState({ platform: null, capabilities: null });
    render(<MessageBubble message={message} />);
    expect((screen.getByRole('button', { name: '撤回' }) as HTMLButtonElement).disabled).toBe(false);
  });
});
