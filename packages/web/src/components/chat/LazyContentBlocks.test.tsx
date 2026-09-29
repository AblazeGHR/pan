// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useSessionStore } from '@/stores/sessionStore';
import { useDetailStore } from '@/stores/detailStore';
import type { Message } from '@/types';
import { ThinkingBlock } from './ThinkingBlock';
import { ThinkingGroup } from './ThinkingGroup';
import { ToolGroup } from './ToolGroup';
import { NonBodyGroup } from './NonBodyGroup';
import { groupChildren } from './nonBodyGroupUtils';
import { LONG_BLOCK_CONTENT_THRESHOLD } from './lazyBlockContent';

const virtualizerHarness = vi.hoisted(() => {
  const listeners = new Set<() => void>();
  return {
    groupCount: 0,
    itemKeys: [] as string[],
    visibleIndexes: null as number[] | null,
    listeners,
    setVisibleIndexes(indexes: number[] | null) {
      this.visibleIndexes = indexes;
      for (const listener of listeners) listener();
    },
    reset() {
      this.groupCount = 0;
      this.itemKeys = [];
      this.visibleIndexes = null;
      listeners.clear();
    },
  };
});

vi.mock('@tanstack/react-virtual', async () => {
  const { useEffect, useReducer } = await import('react');
  return {
    useVirtualizer: (options: {
      count: number;
      getItemKey: (index: number) => string | number;
      estimateSize: (index: number) => number;
      gap: number;
    }) => {
      const [, rerender] = useReducer((value: number) => value + 1, 0);
      virtualizerHarness.groupCount = options.count;
      virtualizerHarness.itemKeys = Array.from({ length: options.count }, (_, index) =>
        String(options.getItemKey(index)),
      );
      useEffect(() => {
        virtualizerHarness.listeners.add(rerender);
        return () => {
          virtualizerHarness.listeners.delete(rerender);
        };
      }, []);

      const visibleIndexes = virtualizerHarness.visibleIndexes ??
        Array.from({ length: Math.min(options.count, 8) }, (_, index) => index);
      return {
        getVirtualItems: () => visibleIndexes
          .filter((index) => index >= 0 && index < options.count)
          .map((index) => ({
            index,
            key: options.getItemKey(index),
            start: index * (options.estimateSize(index) + options.gap),
            size: options.estimateSize(index),
          })),
        getTotalSize: () => options.count === 0
          ? 0
          : options.count * options.estimateSize(0) + (options.count - 1) * options.gap,
        measureElement: () => {},
        measure: () => {},
        scrollToIndex: () => {},
      };
    },
  };
});

vi.mock('./MarkdownRenderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => (
    <div data-testid="rendered-thinking-content">{content.slice(0, 100)}</div>
  ),
}));

afterEach(() => cleanup());

beforeEach(() => {
  virtualizerHarness.reset();
  useSessionStore.setState({ currentSessionId: 'session-1' });
  useDetailStore.setState({ detailTarget: null });
});

describe('lazy long chat blocks', () => {
  it('defers thinking Markdown of any length until expanded', () => {
    const short = render(<ThinkingBlock message={{ role: 'thinking', content: 'short plan' }} />);
    expect(screen.queryByTestId('rendered-thinking-content')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'thinking' }));
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
  it('renders short thinking Markdown only while its group is open or closing', () => {
    const items: Message[] = [
      { role: 'thinking', content: 'first thought', blockId: 'thought-1' },
      { role: 'thinking', content: 'second thought', blockId: 'thought-2' },
    ];
    render(<ThinkingGroup items={items} />);

    const disclosure = screen.getByRole('button', { name: '2 thinking blocks' });
    expect(disclosure.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByTestId('rendered-thinking-content')).toBeNull();

    fireEvent.click(disclosure);
    expect(disclosure.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getAllByTestId('rendered-thinking-content').map((node) => node.textContent))
      .toEqual(['first thought', 'second thought']);
    fireEvent.click(disclosure);
    expect(disclosure.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getAllByTestId('rendered-thinking-content')).toHaveLength(2);
    fireEvent.transitionEnd(screen.getByTestId('thinking-content-window'), { propertyName: 'max-height' });
    expect(screen.queryByTestId('rendered-thinking-content')).toBeNull();
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

  it('virtualizes alternating child runs without mounting folded Markdown', () => {
    const items: Message[] = Array.from({ length: 120 }, (_, index) => ({
      role: index % 2 === 0 ? 'thinking' : 'tool',
      content: index % 2 === 0 ? `short thought ${index}` : 'tool result (Bash)',
      blockId: `long-run-${index}`,
    }));
    const { container } = render(<NonBodyGroup items={items} />);

    expect(container.querySelectorAll('.tool-group, .thinking')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: /120 non-body blocks/ }));
    expect(virtualizerHarness.groupCount).toBe(120);
    expect(container.querySelector('[data-testid="non-body-group-window"]')?.getAttribute('data-group-count'))
      .toBe('120');
    expect(container.querySelectorAll('[data-child-group]')).toHaveLength(8);
    expect(screen.getAllByRole('button', { name: 'thinking' })).toHaveLength(4);
    expect(screen.queryByTestId('rendered-thinking-content')).toBeNull();

    fireEvent.click(screen.getAllByRole('button', { name: 'thinking' })[0]!);
    expect(screen.getAllByTestId('rendered-thinking-content')).toHaveLength(1);

    // A row that leaves the virtual range unmounts. When it returns, its local
    // disclosure state starts folded, matching existing top-level groups.
    act(() => virtualizerHarness.setVisibleIndexes([118, 119]));
    expect(container.querySelectorAll('[data-child-group]')).toHaveLength(2);
    act(() => virtualizerHarness.setVisibleIndexes([0, 1]));
    expect(container.querySelectorAll('[data-child-group]')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'thinking' }).getAttribute('aria-expanded')).toBe('false');
  });

  it('counts one long contiguous run as one virtual row and handles empty or single group lists', () => {
    expect(groupChildren([])).toEqual([]);
    const items: Message[] = Array.from({ length: 240 }, (_, index) => ({
      role: 'tool',
      content: `tool result (${index})`,
      blockId: `one-long-run-${index}`,
    }));
    const { container, rerender } = render(<NonBodyGroup items={items} />);
    fireEvent.click(screen.getByRole('button', { name: /240 non-body blocks/ }));

    expect(virtualizerHarness.groupCount).toBe(1);
    expect(container.querySelectorAll('[data-child-group]')).toHaveLength(1);
    expect(screen.getByRole('button', { name: '240 tools' })).toBeTruthy();

    rerender(<NonBodyGroup items={[items[0]!]} />);
    expect(virtualizerHarness.groupCount).toBe(1);
    expect(container.querySelectorAll('[data-child-group]')).toHaveLength(1);

    rerender(<NonBodyGroup items={[]} />);
    expect(container.querySelector('.non-body-group')).toBeNull();
    expect(container.querySelector('[data-testid="non-body-group-window"]')).toBeNull();
  });

  it('keeps child keys stable as a run streams and existing groups shift after prepend', () => {
    const initial: Message[] = [
      { role: 'tool', content: 'Bash({})', blockId: 'stable-tool-first' },
      { role: 'tool', content: 'Read({})', blockId: 'stable-tool-second' },
      { role: 'thinking', content: 'plan', blockId: 'stable-thinking' },
      { role: 'tool', content: 'Write({})', blockId: 'stable-tool-last' },
    ];
    const { rerender, container } = render(<NonBodyGroup items={initial} />);
    fireEvent.click(screen.getByRole('button', { name: /4 non-body blocks/ }));
    const originalKeys = [...virtualizerHarness.itemKeys];
    expect(originalKeys).toHaveLength(3);

    const streamedFirst = { ...initial[0]!, content: 'Bash({"stream":"updated"})' };
    const appendedTool: Message = { role: 'tool', content: 'Patch({})', blockId: 'stable-tool-appended' };
    rerender(<NonBodyGroup items={[streamedFirst, initial[1]!, appendedTool, ...initial.slice(2)]} />);
    expect(virtualizerHarness.itemKeys).toEqual(originalKeys);
    expect(screen.getByRole('button', { name: '3 tools' })).toBeTruthy();

    const prepended: Message = { role: 'thinking', content: 'older plan', blockId: 'stable-prepended' };
    rerender(<NonBodyGroup items={[prepended, streamedFirst, initial[1]!, appendedTool, ...initial.slice(2)]} />);
    expect(virtualizerHarness.itemKeys.slice(1)).toEqual(originalKeys);
    expect(container.querySelectorAll('[data-child-group]')).toHaveLength(4);
  });

  it('does not parse folded child tool payloads when the outer group opens', () => {
    const args = '{"command":"echo deferred"}';
    const parse = vi.spyOn(JSON, 'parse');
    const tool: Message = {
      role: 'tool',
      content: `tool call: Bash\nargs: ${args}`,
      blockId: 'lazy-child-tool',
    };
    const { container } = render(<NonBodyGroup items={[tool]} />);
    fireEvent.click(screen.getByRole('button', { name: /1 non-body blocks/ }));

    expect(container.querySelectorAll('.tool-group')).toHaveLength(1);
    expect(parse).not.toHaveBeenCalledWith(args);
    fireEvent.click(screen.getByRole('button', { name: '1 tools' }));
    expect(parse).toHaveBeenCalledWith(args);
    parse.mockRestore();
  });
});
