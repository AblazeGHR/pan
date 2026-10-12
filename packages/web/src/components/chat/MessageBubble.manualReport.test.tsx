// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { MessageBubble } from './MessageBubble';
import type { ManualReportTarget } from './manualTaskReport';

vi.mock('./MarkdownRenderer', () => ({ MarkdownRenderer: ({ content }: { content: string }) => <span>{content}</span> }));

const TARGET: ManualReportTarget = { sessionId: 'ses_ta', taskId: 'task-2', messageId: 'pan:a2' };

const tailBlock: Message = {
  role: 'assistant',
  content: 'final answer of task 2',
  messageId: 'pan:a2',
  ts: '2026-10-06T12:00:00',
};
const olderBlock: Message = { ...tailBlock, content: 'final answer of task 1', messageId: 'pan:a1' };

const originalReport = useSessionStore.getState().reportTaskManually;

function setReportEntry(state: 'sending' | 'sent' | 'failed' | 'uncertain', error?: string): void {
  useSessionStore.setState({
    manualTaskReports: {
      [`ses_ta:pan:a2`]: { state, ...(error ? { error } : {}), taskId: 'task-2' },
    },
  });
}

beforeEach(() => {
  useSessionStore.setState({ manualTaskReports: {} });
});

afterEach(() => {
  cleanup();
  useSessionStore.setState({ manualTaskReports: {}, reportTaskManually: originalReport });
});

describe('manual task report action placement', () => {
  it('shows the action only on the bound tail block', () => {
    render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    expect(screen.getByRole('button', { name: '汇报' })).not.toBeNull();

    cleanup();
    render(<MessageBubble message={olderBlock} manualReportTarget={TARGET} />);
    expect(screen.queryByRole('button', { name: '汇报' })).toBeNull();
  });

  it('shows no action without a binding', () => {
    render(<MessageBubble message={tailBlock} />);
    expect(screen.queryByRole('button', { name: '汇报' })).toBeNull();
  });

  it('never shows the action on user rows', () => {
    render(<MessageBubble
      message={{ role: 'user', content: 'assign text', messageId: 'pan:u1', ts: '2026-10-06T12:00:00' }}
      manualReportTarget={TARGET} />);
    expect(screen.queryByRole('button', { name: '汇报' })).toBeNull();
  });

  it('keeps the bare timestamp layout on non-target assistant rows', () => {
    render(<MessageBubble message={olderBlock} manualReportTarget={TARGET} />);
    expect(document.querySelector('[data-message-meta]')).toBeNull();
    expect(document.querySelector('time')).not.toBeNull();
  });

  it('places the action before the timestamp in one meta row', () => {
    render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    const button = screen.getByRole('button', { name: '汇报' });
    const timestamp = document.querySelector('time')!;
    expect(button.parentElement).toBe(timestamp.parentElement);
    expect(button.nextElementSibling).toBe(timestamp);
    expect(button.parentElement?.className).toContain('flex items-center');
  });

  it('reports with the identity captured in the binding', () => {
    const report = vi.fn();
    useSessionStore.setState({ reportTaskManually: report });
    render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    fireEvent.click(screen.getByRole('button', { name: '汇报' }));
    expect(report).toHaveBeenCalledWith('ses_ta', { taskId: 'task-2', messageId: 'pan:a2' });
  });

  it('forwards a null taskId binding unchanged', () => {
    const report = vi.fn();
    useSessionStore.setState({ reportTaskManually: report });
    render(<MessageBubble
      message={tailBlock}
      manualReportTarget={{ sessionId: 'ses_ta', taskId: null, messageId: 'pan:a2' }} />);
    fireEvent.click(screen.getByRole('button', { name: '汇报' }));
    expect(report).toHaveBeenCalledWith('ses_ta', { taskId: null, messageId: 'pan:a2' });
  });
});

describe('manual task report action states', () => {
  it('disables the action and shows progress while sending', () => {
    setReportEntry('sending');
    render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    const button = screen.getByRole('button', { name: '汇报中…' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it('shows a terminal reported state and stays disabled', () => {
    setReportEntry('sent');
    render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    const button = screen.getByRole('button', { name: '已汇报' }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it('offers a retry after a definite failure', () => {
    setReportEntry('failed', '任务已变化，请刷新后重试');
    render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    const button = screen.getByRole('button', { name: '重试汇报' }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.title).toBe('任务已变化，请刷新后重试');
  });

  it('offers a retry after an uncertain outcome without promising a single send', () => {
    setReportEntry('uncertain', '汇报结果不确定（HTTP 500: boom），请重试确认');
    render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    const button = screen.getByRole('button', { name: '重试汇报' }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    expect(button.title).toContain('幂等');
  });

  it('retry click re-invokes the action with the same identity', () => {
    const report = vi.fn();
    useSessionStore.setState({ reportTaskManually: report });
    setReportEntry('failed', '汇报落盘失败: disk full');
    render(<MessageBubble message={tailBlock} manualReportTarget={TARGET} />);
    fireEvent.click(screen.getByRole('button', { name: '重试汇报' }));
    expect(report).toHaveBeenCalledWith('ses_ta', { taskId: 'task-2', messageId: 'pan:a2' });
  });
});
