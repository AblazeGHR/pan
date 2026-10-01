// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { RewindStatusBar } from './RewindStatusBar';
import { useSessionStore, type ActiveRewind } from '@/stores/sessionStore';
import type { Session } from '@/types';

const anchor = { role: 'user' as const, content: 'anchor', messageId: 'm1' };
const sessions: Session[] = [
  { id: 's1', name: '\u5f53\u524d\u4f1a\u8bdd', history: [], alwaysThinkingEnabled: false, effort: 'medium' },
  { id: 's2', name: '\u53e6\u4e00\u4e2a\u4f1a\u8bdd', history: [], alwaysThinkingEnabled: false, effort: 'medium' },
];

function item(patch: Partial<ActiveRewind> = {}): ActiveRewind {
  return {
    jobId: 'j1', sessionId: 's1', anchorText: 'anchor', anchorMessage: anchor,
    scope: 1, stage: 'resuming', status: 'running', ...patch,
  };
}

describe('RewindStatusBar', () => {
  beforeEach(() => {
    useSessionStore.setState({
      activeRewinds: [], sessions, currentSessionId: 's1',
      openRewindProgress: vi.fn(async () => {}),
      completeRewind: vi.fn(async () => {}),
      dismissRewind: vi.fn(),
    });
  });
  afterEach(() => cleanup());

  it('does not render when there are no rewind jobs', () => {
    render(<RewindStatusBar />);
    expect(screen.queryByTestId('rewind-status-bar')).toBeNull();
  });

  it('renders current and other-session jobs together', () => {
    useSessionStore.setState({ activeRewinds: [
      item(), item({ jobId: 'j2', sessionId: 's2', stage: 'truncating' }),
    ] });
    render(<RewindStatusBar />);
    expect(screen.getAllByText(/\u64a4\u56de\u8fdb\u884c\u4e2d/)).toHaveLength(2);
    expect(screen.getByText(/\u4f1a\u8bdd\u300c\u53e6\u4e00\u4e2a\u4f1a\u8bdd\u300d/)).toBeTruthy();
    expect(screen.getAllByRole('button', { name: '\u67e5\u770b' })).toHaveLength(2);
  });

  it('reopens a running job from ??', () => {
    const open = vi.fn(async () => {});
    useSessionStore.setState({ activeRewinds: [item()], openRewindProgress: open });
    render(<RewindStatusBar />);
    fireEvent.click(screen.getByRole('button', { name: '\u67e5\u770b' }));
    expect(open).toHaveBeenCalledWith('j1', 's1');
  });

  it('routes a completed entry to the deferred branch jump', () => {
    const complete = vi.fn(async () => {});
    useSessionStore.setState({
      activeRewinds: [item({ status: 'completed', stage: 'completed', newSessionId: 's3' })],
      completeRewind: complete,
    });
    render(<RewindStatusBar />);
    fireEvent.click(screen.getByRole('button', { name: '\u67e5\u770b' }));
    expect(complete).toHaveBeenCalledWith('j1');
  });

  it('keeps failures until the user closes the entry', () => {
    const dismiss = vi.fn();
    useSessionStore.setState({ activeRewinds: [item({ status: 'failed', stage: 'failed', error: '\u64a4\u56de\u5931\u8d25' })], dismissRewind: dismiss });
    render(<RewindStatusBar />);
    expect(screen.getByText('\u64a4\u56de\u5931\u8d25')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '\u5173\u95ed\u64a4\u56de\u9519\u8bef' }));
    expect(dismiss).toHaveBeenCalledWith('j1', 's1');
  });
});

