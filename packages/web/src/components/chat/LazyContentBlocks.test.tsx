// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import { useDetailStore } from '@/stores/detailStore';
import type { Message } from '@/types';
import { ThinkingBlock } from './ThinkingBlock';
import { ToolGroup } from './ToolGroup';
import { LONG_BLOCK_CONTENT_THRESHOLD } from './lazyBlockContent';

vi.mock('./MarkdownRenderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => (
    <div data-testid="rendered-thinking-content">{content.slice(0, 100)}</div>
  ),
}));

afterEach(() => cleanup());

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: 'session-1' });
  useDetailStore.setState({ detailTarget: null });
});

describe('lazy long chat blocks', () => {
  it('keeps short thinking content eager and defers long Markdown until expanded', () => {
    const short = render(<ThinkingBlock message={{ role: 'thinking', content: 'short plan' }} />);
    expect(screen.getByTestId('rendered-thinking-content').textContent).toBe('short plan');
    short.unmount();

    const longContent = 'x'.repeat(LONG_BLOCK_CONTENT_THRESHOLD + 1);
    render(<ThinkingBlock message={{ role: 'thinking', content: longContent }} />);
    expect(screen.queryByTestId('rendered-thinking-content')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'thinking' }));
    expect(screen.getByTestId('rendered-thinking-content').textContent).toBe('x'.repeat(100));
    expect(screen.getByRole('button', { name: 'thinking' }).getAttribute('aria-expanded')).toBe('true');
  });

  it('unmounts long thinking Markdown after its collapse transition and accepts stream updates while open', () => {
    const longContent = 'x'.repeat(LONG_BLOCK_CONTENT_THRESHOLD + 1);
    const initial: Message = { role: 'thinking', content: longContent, blockId: 'thinking-block' };
    const { rerender, container } = render(<ThinkingBlock message={initial} />);

    fireEvent.click(screen.getByRole('button', { name: 'thinking' }));
    const updated = { ...initial, content: `streamed ${'y'.repeat(LONG_BLOCK_CONTENT_THRESHOLD + 1)}` };
    rerender(<ThinkingBlock message={updated} />);
    expect(screen.getByTestId('rendered-thinking-content').textContent).toBe(updated.content.slice(0, 100));

    fireEvent.click(screen.getByRole('button', { name: 'thinking' }));
    const window = container.querySelector('[data-testid="thinking-content-window"]');
    expect(window).not.toBeNull();
    fireEvent.transitionEnd(window!, { propertyName: 'max-height' });
    expect(screen.queryByTestId('rendered-thinking-content')).toBeNull();
  });

  it('shows a long tool payload in a bounded internal scroll viewport on demand', () => {
    const longValue = 'payload'.repeat(Math.ceil(LONG_BLOCK_CONTENT_THRESHOLD / 7));
    const tool: Message = {
      role: 'tool',
      content: `tool call: Bash\nargs: ${JSON.stringify({ command: longValue })}`,
      messageId: 'tool-message-1',
    };
    render(<ToolGroup items={[tool]} />);

    expect(screen.queryByLabelText('Bash content')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '1 tools' }));
    expect(screen.queryByRole('region', { name: 'Bash content' })).toBeNull();
    fireEvent.click(screen.getByText('Bash'));

    const content = screen.getByRole('region', { name: 'Bash content' });
    expect(content.className).toContain('max-h-[20rem]');
    expect(content.className).toContain('overflow-y-auto');
    expect(content.textContent).toContain(longValue);
    expect(useDetailStore.getState().detailTarget).toEqual({
      type: 'tool',
      content: tool.content,
      title: 'Bash',
    });
  });
});
