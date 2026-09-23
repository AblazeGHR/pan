// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import { useDetailStore } from '@/stores/detailStore';
import type { Message } from '@/types';
import { ThinkingBlock } from './ThinkingBlock';
import { ThinkingGroup } from './ThinkingGroup';
import { ToolGroup } from './ToolGroup';
import { NonBodyGroup } from './NonBodyGroup';
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
  it('toggles adjacent short thinking blocks as one group while keeping short Markdown eager', () => {
    const items: Message[] = [
      { role: 'thinking', content: 'first thought', blockId: 'thought-1' },
      { role: 'thinking', content: 'second thought', blockId: 'thought-2' },
    ];
    render(<ThinkingGroup items={items} />);

    const disclosure = screen.getByRole('button', { name: '2 thinking blocks' });
    expect(disclosure.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getAllByTestId('rendered-thinking-content').map((node) => node.textContent))
      .toEqual(['first thought', 'second thought']);

    fireEvent.click(disclosure);
    expect(disclosure.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(disclosure);
    expect(disclosure.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getAllByTestId('rendered-thinking-content')).toHaveLength(2);
  });

  it('defers long grouped thinking, keeps an open group across streamed appends, then unloads after collapse', () => {
    const items: Message[] = [
      { role: 'thinking', content: 'a'.repeat(13_000), blockId: 'long-thought-1' },
      { role: 'thinking', content: 'b'.repeat(13_000), blockId: 'long-thought-2' },
    ];
    const { rerender, container } = render(<ThinkingGroup items={items} />);
    expect(screen.queryByTestId('rendered-thinking-content')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '2 thinking blocks' }));
    const streamed = { ...items[0]!, content: `${items[0]!.content} streamed` };
    const third: Message = { role: 'thinking', content: 'third streamed thought', blockId: 'long-thought-3' };
    rerender(<ThinkingGroup items={[streamed, items[1]!, third]} />);

    const disclosure = screen.getByRole('button', { name: '3 thinking blocks' });
    expect(disclosure.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getAllByTestId('rendered-thinking-content')).toHaveLength(3);
    expect(screen.getByText('third streamed thought')).toBeTruthy();
    expect(container.querySelector('[data-testid="thinking-content-window"] > div')?.className)
      .toContain('max-h-40 overflow-y-auto');
    expect(container.querySelector('[data-testid="thinking-content-window"] > div')?.className)
      .not.toContain('overscroll-contain');

    fireEvent.click(disclosure);
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
    expect(content.className).not.toContain('overscroll-contain');
    expect(content.textContent).toContain(longValue);
    expect(useDetailStore.getState().detailTarget).toEqual({
      type: 'tool',
      content: tool.content,
      title: 'Bash',
    });
  });

  it('keeps long tool and thinking children lazy with their bounded viewports inside a merged parent', () => {
    const longThinking: Message = {
      role: 'thinking',
      content: 'plan '.repeat(5_000),
      blockId: 'merged-parent-long-thinking',
    };
    const longValue = 'payload'.repeat(Math.ceil(LONG_BLOCK_CONTENT_THRESHOLD / 7));
    const longTool: Message = {
      role: 'tool',
      content: `tool call: Bash\nargs: ${JSON.stringify({ command: longValue })}`,
      blockId: 'merged-parent-long-tool',
    };
    const { container } = render(<NonBodyGroup items={[longThinking, longTool]} />);

    expect(screen.queryByTestId('rendered-thinking-content')).toBeNull();
    expect(screen.queryByLabelText('Bash content')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /2 non-body blocks/ }));
    const groupWindow = screen.getByTestId('non-body-group-window');
    expect(groupWindow.className).toContain('max-h-[20rem]');
    expect(groupWindow.className).toContain('overflow-y-auto');
    // Keep native scroll chaining enabled at this boundary for wheel and touch input.
    expect(groupWindow.className).not.toContain('overscroll-contain');
    expect(screen.getByRole('button', { name: 'thinking' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '1 tools' })).toBeTruthy();
    expect(screen.queryByTestId('rendered-thinking-content')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'thinking' }));
    const thinkingWindow = container.querySelector('[data-testid="thinking-content-window"] > div');
    expect(thinkingWindow?.className).toContain('max-h-40 overflow-y-auto');
    expect(thinkingWindow?.className).not.toContain('overscroll-contain');
    expect(screen.getByTestId('rendered-thinking-content').textContent)
      .toBe(longThinking.content.slice(0, 100));

    fireEvent.click(screen.getByRole('button', { name: '1 tools' }));
    fireEvent.click(screen.getByText('Bash'));
    const toolWindow = screen.getByRole('region', { name: 'Bash content' });
    expect(toolWindow.className).toContain('max-h-[20rem]');
    expect(toolWindow.className).toContain('overflow-y-auto');
    expect(toolWindow.className).not.toContain('overscroll-contain');
    expect(toolWindow.textContent).toContain(longValue);
    expect(useDetailStore.getState().detailTarget).toEqual({
      type: 'tool',
      content: longTool.content,
      title: 'Bash',
    });
  });
});
