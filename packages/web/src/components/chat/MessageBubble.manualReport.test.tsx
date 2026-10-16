// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useBookmarkStore } from '@/stores/bookmarkStore';
import { MessageBubble } from './MessageBubble';
import type { ManualReportTarget } from './manualTaskReport';
import { MarkdownRenderer } from './MarkdownRenderer';

vi.mock('./MarkdownRenderer', () => ({ MarkdownRenderer: vi.fn(({ content }: { content: string }) => <span>{content}</span>) }));

const TAIL_ID = `pan:${'a'.repeat(30)}a2`;
const OLDER_ID = `pan:${'a'.repeat(30)}a1`;

const TARGET: ManualReportTarget = {
  sessionId: 'ses_ta', taskId: 'task-2', taskSeq: 2, messageId: TAIL_ID,
};

const tailBlock: Message = {
  role: 'assistant',
  content: 'final answer of task 2',
  messageId: TAIL_ID,
  ts: '2026-10-06T12:00:00',
};
const olderBlock: Message = { ...tailBlock, content: 'final answer of task 1', messageId: OLDER_ID };

const originalReport = useSessionStore.getState().reportTaskManually;

function setReportEntry(state: 'sending' | 'sent' | 'failed' | 'uncertain', error?: string): void {
  useSessionStore.setState({
    manualTaskReports: {
      ['ses_ta:task-2:2']: { state, ...(error ? { error } : {}), taskId: 'task-2', taskSeq: 2 },
    },
  });
}

/** The report button lives in the shared block menu now: open it via the
 *  row's left gutter first. */
function openBlockMenu(container: HTMLElement): void {
  const gutter = container.querySelector<HTMLButtonElement>('[data-message-bookmark-gutter]');
  expect(gutter).not.toBeNull();
  fireEvent.click(gutter!);
}

beforeEach(() => {
  useSessionStore.setState({ manualTaskReports: {}, currentSessionId: 'ses_ta' });
  useBookmarkStore.setState({ sessions: {} });
});

afterEach(() => {
  cleanup();
  useSessionStore.setState({ manualTaskReports: {}, reportTaskManually: originalReport });
  useBookmarkStore.setState({ sessions: {} });
});

describe('manual task report inside the block menu', () => {
  it('does not rerender an unchanged body for recreated report bindings or sidebar preview updates', () => {
    useSessionStore.setState({ sessions: [{
      id: 'ses_ta', name: 'TA', history: [], effort: '', alwaysThinkingEnabled: false,
      workerStatus: 'idle', adapter: 'codex',
    }] });
    vi.mocked(MarkdownRenderer).mockClear();
    const view = render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    const before = vi.mocked(MarkdownRenderer).mock.calls.length;
    view.rerender(<MessageBubble message={tailBlock} manualReportTarget={{ ...TARGET }} />);
    act(() => useSessionStore.setState(state => ({ sessions: state.sessions.map(session => ({ ...session, lastMessage: 'new streaming preview' })) })));
    expect(vi.mocked(MarkdownRenderer).mock.calls.length).toBe(before);
    // Removing the binding removes the entry (the body itself is unchanged).
    view.rerender(<MessageBubble message={tailBlock} manualReportTarget={null} />);
    expect(view.container.querySelector('[data-manual-report]')).toBeNull();
  });

  it('offers the report only on the bound tail block', () => {
    const bound = render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    openBlockMenu(bound.container);
    expect(screen.getByRole('menuitem', { name: '手动汇报' })).not.toBeNull();

    cleanup();
    const other = render(<MessageBubble message={olderBlock} manualReportTarget={TARGET} />);
    openBlockMenu(other.container);
    expect(screen.queryByRole('menuitem', { name: '手动汇报' })).toBeNull();
    // The unbound row's menu still carries the bookmark action.
    expect(screen.getByRole('menuitem', { name: 'Add bookmark' })).not.toBeNull();
  });

  it('shows no entry at all without a binding on a non-bookmarkable row', () => {
    const view = render(<MessageBubble message={{ role: 'assistant', content: 'runtime row' }} />);
    expect(view.container.querySelector('[data-message-bookmark-gutter]')).toBeNull();
    expect(view.container.querySelector('[data-message-block-menu]')).toBeNull();
  });

  it('never shows a report entry on user rows', () => {
    const view = render(<MessageBubble
      message={{ role: 'user', content: 'assign text', messageId: TAIL_ID, ts: '2026-10-06T12:00:00' }}
      manualReportTarget={TARGET} />);
    openBlockMenu(view.container);
    expect(screen.queryByRole('menuitem', { name: '手动汇报' })).toBeNull();
  });

  it('keeps the bare timestamp layout on non-target assistant rows', () => {
    render(<MessageBubble message={olderBlock} manualReportTarget={TARGET} />);
    expect(document.querySelector('[data-message-meta]')).toBeNull();
    expect(document.querySelector('time')).not.toBeNull();
  });

  it('reports with the identity captured in the binding', () => {
    const report = vi.fn();
    useSessionStore.setState({ reportTaskManually: report });
    const view = render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    openBlockMenu(view.container);
    fireEvent.click(screen.getByRole('menuitem', { name: '手动汇报' }));
    expect(report).toHaveBeenCalledWith('ses_ta', { taskId: 'task-2', taskSeq: 2, messageId: TAIL_ID });
    // The menu closes after acting.
    expect(document.querySelector('[data-message-block-menu]')).toBeNull();
  });

  it('forwards a binding with an unknown sequence unchanged', () => {
    const report = vi.fn();
    useSessionStore.setState({ reportTaskManually: report });
    const view = render(<MessageBubble
      message={tailBlock}
      manualReportTarget={{ sessionId: 'ses_ta', taskId: 'task-1', taskSeq: null, messageId: TAIL_ID }} />);
    openBlockMenu(view.container);
    fireEvent.click(screen.getByRole('menuitem', { name: '手动汇报' }));
    expect(report).toHaveBeenCalledWith('ses_ta', { taskId: 'task-1', taskSeq: null, messageId: TAIL_ID });
  });
});

describe('manual task report action states', () => {
  it('disables the action and shows progress while sending', () => {
    setReportEntry('sending');
    const view = render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    openBlockMenu(view.container);
    const button = screen.getByRole('menuitem', { name: '汇报中…' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it('shows a terminal reported state and stays disabled', () => {
    setReportEntry('sent');
    const view = render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    openBlockMenu(view.container);
    const button = screen.getByRole('menuitem', { name: '已汇报' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it('offers a retry after a definite failure', () => {
    setReportEntry('failed', '任务已变化，请刷新后重试');
    const view = render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    openBlockMenu(view.container);
    const button = screen.getByRole('menuitem', { name: '重试汇报' }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.title).toBe('任务已变化，请刷新后重试');
  });

  it('offers a retry after an uncertain outcome without promising a single send', () => {
    setReportEntry('uncertain', '汇报结果不确定（HTTP 500: boom），请重试确认');
    const view = render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    openBlockMenu(view.container);
    const button = screen.getByRole('menuitem', { name: '重试汇报' }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.title).toContain('幂等');
  });

  it('retry click re-invokes the action with the same identity', () => {
    const report = vi.fn();
    useSessionStore.setState({ reportTaskManually: report });
    setReportEntry('failed', '汇报落盘失败: disk full');
    const view = render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    openBlockMenu(view.container);
    fireEvent.click(screen.getByRole('menuitem', { name: '重试汇报' }));
    expect(report).toHaveBeenCalledWith('ses_ta', { taskId: 'task-2', taskSeq: 2, messageId: TAIL_ID });
  });
});
