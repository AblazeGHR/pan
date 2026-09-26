// @vitest-environment jsdom
// Regression coverage for the removed "unread block" highlight: collapsed
// thinking / tool / non-body groups must never paint an unread dot, the store
// must no longer expose unread state, and normal grouping must stay intact.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { ThinkingGroup } from './ThinkingGroup';
import { ToolGroup } from './ToolGroup';
import { NonBodyGroup } from './NonBodyGroup';

vi.mock('./MarkdownRenderer', () => ({
  MarkdownRenderer: ({ content }: { content: string }) => <div>{content}</div>,
}));

afterEach(() => cleanup());

const thinking = (label: string, blockId: string): Message => ({
  role: 'thinking',
  content: label,
  blockId,
});

const tool = (name: string, command: string, blockId: string): Message => ({
  role: 'tool',
  content: `tool call: ${name}\nargs: ${JSON.stringify({ command })}`,
  blockId,
});

/** Elements that would render as a highlight dot inside a group disclosure. */
function highlightNodes(scope: HTMLElement): NodeListOf<Element> {
  return scope.querySelectorAll('[title="unread"], .rounded-full');
}

function disclosure(name: string | RegExp): HTMLElement {
  return screen.getByRole('button', { name });
}

/**
 * Write the pre-removal `sessionUnread` map straight onto the store state so a
 * residual consumer of it would still find data. Returns a restore callback:
 * the key is not part of the store type any more.
 */
function injectLegacyUnread(map: Record<string, Set<string>>): () => void {
  const state = useSessionStore.getState() as unknown as Record<string, unknown>;
  state.sessionUnread = map;
  return () => { delete state.sessionUnread; };
}

/**
 * jsdom has no layout engine, so the thinking content window is given explicit
 * scroll metrics to make the streaming-follow contract observable.
 */
function stubContentWindow(container: HTMLElement) {
  const content = container.querySelector('[data-testid="thinking-content-window"] > div');
  expect(content).not.toBeNull();
  const element = content as HTMLElement;
  let scrollTop = 0;
  let scrollHeight = 400;
  Object.defineProperty(element, 'scrollTop', {
    configurable: true,
    get: () => scrollTop,
    set: (value: number) => { scrollTop = value; },
  });
  Object.defineProperty(element, 'scrollHeight', {
    configurable: true,
    get: () => scrollHeight,
  });
  return {
    get scrollTop() { return scrollTop; },
    setScrollHeight(value: number) { scrollHeight = value; },
  };
}

describe('chat groups render no unread highlight', () => {
  it('never marks a collapsed thinking group, including after streamed appends', () => {
    const first = thinking('plan one', 'think-1');
    const { rerender } = render(<ThinkingGroup items={[first]} />);
    expect(highlightNodes(disclosure('thinking'))).toHaveLength(0);

    rerender(<ThinkingGroup items={[first, thinking('plan two', 'think-2')]} />);

    const group = disclosure('2 thinking blocks');
    expect(group.getAttribute('aria-expanded')).toBe('false');
    expect(highlightNodes(group)).toHaveLength(0);
  });

  it('never marks a collapsed tool group, including after streamed appends', () => {
    const first = tool('Bash', 'true', 'tool-1');
    const { rerender } = render(<ToolGroup items={[first]} />);
    expect(highlightNodes(disclosure('1 tools'))).toHaveLength(0);

    rerender(<ToolGroup items={[first, tool('Read', 'file.txt', 'tool-2')]} />);

    const group = disclosure('2 tools');
    expect(group.getAttribute('aria-expanded')).toBe('false');
    expect(highlightNodes(group)).toHaveLength(0);
  });

  it('never marks a collapsed merged non-body group', () => {
    const { rerender } = render(<NonBodyGroup items={[thinking('plan', 'merged-1')]} />);
    expect(highlightNodes(disclosure(/1 non-body blocks/))).toHaveLength(0);

    rerender(<NonBodyGroup items={[
      thinking('plan', 'merged-1'),
      tool('Bash', 'true', 'merged-2'),
    ]} />);

    const group = disclosure(/2 non-body blocks/);
    expect(group.getAttribute('aria-expanded')).toBe('false');
    expect(highlightNodes(group)).toHaveLength(0);
  });

  it('drops the unread state and actions from the session store', () => {
    const state = useSessionStore.getState();
    for (const key of ['sessionUnread', 'getUnread', 'markUnread', 'clearUnread']) {
      expect(key in state).toBe(false);
    }
  });

  it('stays inert when the legacy unread map is injected into the store', () => {
    // Before the removal a collapsed group painted a dot when one of its blocks
    // was in sessionUnread[...]. Injecting that legacy shape must render
    // nothing, which pins that no consumer of the removed state survives.
    const restore = injectLegacyUnread({ 'session-1': new Set(['plan one']) });
    try {
      // The injection must really land, otherwise this test proves nothing.
      const injected = useSessionStore.getState() as unknown as Record<string, unknown>;
      expect(injected.sessionUnread).toBeDefined();

      const first = thinking('plan one', 'think-1');
      const { container } = render(
        <>
          <ThinkingGroup items={[first]} />
          <ToolGroup items={[tool('Bash', 'true', 'tool-1')]} />
          <NonBodyGroup items={[first, tool('Bash', 'true', 'tool-1')]} />
        </>,
      );

      expect(highlightNodes(container)).toHaveLength(0);
    } finally {
      restore();
    }
  });
});

describe('normal group behaviour is preserved', () => {
  it('uses right chevrons while folded and down chevrons while expanded', () => {
    const groupItems = [thinking('plan', 'arrow-thinking'), tool('Bash', 'true', 'arrow-tool')];
    render(<NonBodyGroup items={groupItems} />);

    const nonBody = disclosure(/2 non-body blocks/);
    expect(nonBody.querySelector('svg')?.classList.contains('lucide-chevron-right')).toBe(true);
    fireEvent.click(nonBody);
    expect(nonBody.querySelector('svg')?.classList.contains('lucide-chevron-down')).toBe(true);

    const thinkingGroup = disclosure('thinking');
    expect(thinkingGroup.querySelector('svg')?.classList.contains('lucide-chevron-right')).toBe(true);
    fireEvent.click(thinkingGroup);
    expect(thinkingGroup.querySelector('svg')?.classList.contains('lucide-chevron-down')).toBe(true);

    const toolGroup = disclosure('1 tools');
    expect(toolGroup.querySelector('svg.lucide-chevron-right')).not.toBeNull();
    fireEvent.click(toolGroup);
    expect(toolGroup.querySelector('svg.lucide-chevron-down')).not.toBeNull();

    const toolRow = screen.getByText('Bash').closest('.msg');
    expect(toolRow?.querySelector('svg.lucide-chevron-right')).not.toBeNull();
    if (toolRow) fireEvent.click(toolRow);
    expect(toolRow?.querySelector('svg.lucide-chevron-down')).not.toBeNull();
  });

  it('keeps a thinking group collapsed by default and reveals every member on expand', () => {
    render(<ThinkingGroup items={[thinking('first thought', 't1'), thinking('second thought', 't2')]} />);

    const group = disclosure('2 thinking blocks');
    expect(group.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByText('first thought')).toBeTruthy();
    expect(screen.getByText('second thought')).toBeTruthy();

    fireEvent.click(group);
    expect(group.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(group);
    expect(group.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByText('second thought')).toBeTruthy();
  });

  it('keeps the tool count and the tool rows on expand', () => {
    render(<ToolGroup items={[tool('Bash', 'true', 'tool-1'), tool('Read', 'file.txt', 'tool-2')]} />);

    const group = disclosure('2 tools');
    expect(group.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText('Bash')).toBeNull();

    fireEvent.click(group);
    expect(group.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByText('Bash')).toBeTruthy();
    expect(screen.getByText('Read')).toBeTruthy();
  });

  it('keeps the merged non-body summary and its child groups', () => {
    render(<NonBodyGroup items={[
      thinking('plan', 'merged-1'),
      tool('Bash', 'true', 'merged-2'),
    ]} />);

    const group = disclosure(/2 non-body blocks/);
    expect(group.textContent).toContain('1 tool');
    expect(group.textContent).toContain('1 thinking block');
    expect(screen.queryByTestId('non-body-group-window')).toBeNull();

    fireEvent.click(group);
    expect(screen.getByTestId('non-body-group-window')).toBeTruthy();
    expect(screen.getByText('plan')).toBeTruthy();
    expect(disclosure('1 tools')).toBeTruthy();
  });

  it('follows an open group while its thinking content streams', () => {
    const first = thinking('plan one', 't1');
    const { container, rerender } = render(<ThinkingGroup items={[first]} />);
    const window = stubContentWindow(container);

    fireEvent.click(disclosure('thinking'));
    rerender(<ThinkingGroup items={[first, thinking('plan two', 't2')]} />);
    expect(window.scrollTop).toBe(400);

    window.setScrollHeight(512);
    rerender(<ThinkingGroup items={[first, thinking('plan two plus a streamed suffix', 't2')]} />);
    expect(window.scrollTop).toBe(512);
  });

  it('does not scroll the content window when a finished group is expanded', () => {
    const items = [thinking('plan one', 't1'), thinking('plan two', 't2')];
    const { container, rerender } = render(<ThinkingGroup items={[items[0]!]} />);
    const window = stubContentWindow(container);

    // Appends that land while the group is collapsed must not leave a pending
    // jump for the later expand.
    rerender(<ThinkingGroup items={items} />);
    fireEvent.click(disclosure('2 thinking blocks'));

    expect(window.scrollTop).toBe(0);
    expect(disclosure('2 thinking blocks').getAttribute('aria-expanded')).toBe('true');
  });
});
