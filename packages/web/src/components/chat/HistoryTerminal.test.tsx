// @vitest-environment jsdom
import { expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageDisplayItem } from './MessageBubble';
import type { Message } from '@/types';

it('renders errors outside assistant/Markdown bubbles with expandable plain diagnostics', () => {
  const row: Message = { role: 'terminal', kind: 'terminal', content: '', terminal: {
    eventId: 'terminal:1', terminalKey: '1', status: 'error', occurredAt: '2026-10-27T00:00:00Z',
    order: 1, anchorMessageId: null, taskId: null, taskSeq: 1, executionSeq: 1,
    workerId: 'w', generation: 1, error: { source: 'cli.error', code: 429,
      message: 'Capacity\n**literal diagnostic** <script>text</script>' },
  } };
  const { container, rerender } = render(<MessageDisplayItem item={row} />);
  expect(screen.getByText('执行出错')).toBeTruthy();
  expect(screen.getByText('Capacity')).toBeTruthy();
  expect(container.querySelector('details')?.open).toBe(false);
  expect(container.querySelector('pre')?.textContent).toContain('**literal diagnostic**');
  expect(container.querySelector('script')).toBeNull();
  expect(container.querySelector('[data-terminal-status="error"]')).toBeTruthy();
  rerender(<MessageDisplayItem item={{ ...row, terminal: { ...row.terminal!, status: 'done' } }} />);
  expect(screen.getByText('已完成')).toBeTruthy();
  expect(container.querySelector('details')).toBeNull();
});
