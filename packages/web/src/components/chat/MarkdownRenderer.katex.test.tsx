// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { MarkdownRenderer } from './MarkdownRenderer';
import { SearchTextContext } from './searchText';

afterEach(cleanup);

function message(content: string, query = '') {
  return <MemoryRouter><SearchTextContext.Provider value={{ query, occurrence: 0 }}>
    <MarkdownRenderer content={content} />
  </SearchTextContext.Provider></MemoryRouter>;
}

describe('MarkdownRenderer dollar math', () => {
  it('renders inline math as accessible MathML and visual KaTeX', () => {
    const { container } = render(message('Before $x^2 + y^2$ after.'));
    expect(container.querySelectorAll('.katex')).toHaveLength(1);
    expect(container.querySelector('math')?.getAttribute('display')).not.toBe('block');
    expect(container.querySelector('annotation')?.textContent).toBe('x^2 + y^2');
    expect(container.querySelector('.katex-html')).toBeTruthy();
    expect(container.textContent).toContain('Before ');
    expect(container.textContent).toContain(' after.');
  });

  it('renders a standalone double-dollar block', () => {
    const { container } = render(message('Before\n\n$$\n\\frac{a}{b}\n$$\n\nAfter'));
    expect(container.querySelectorAll('.katex-display')).toHaveLength(1);
    expect(container.querySelector('math')?.getAttribute('display')).toBe('block');
    expect(container.querySelector('annotation')?.textContent).toBe('\\frac{a}{b}');
    expect(container.querySelectorAll('p')).toHaveLength(2);
  });

  it('protects inline, plain, js and latex fenced code; preserves existing math-fence rendering', () => {
    const { container } = render(message('`$literal$`\n\n```\n$plain$\n```\n\n```js\nconst cost = "$5";\n```\n\n```latex\n$x^2$\n```\n\n```math\nx^2\n```'));
    expect(container.querySelectorAll('.katex-display')).toHaveLength(1);
    expect(container.querySelector('annotation')?.textContent?.trim()).toBe('x^2');
    expect([...container.querySelectorAll('code')].map(node => node.textContent)).toEqual([
      '$literal$', '$plain$\n', 'const cost = "$5";\n', '$x^2$\n',
    ]);
  });

  it('contains invalid formulas without losing the rest of the message', () => {
    const { container } = render(message('Before $\\frac{$ after.\n\n**Still visible** $z$'));
    expect(container.querySelector('.katex-error')?.textContent).toBe('\\frac{');
    expect(container.querySelector('strong')?.textContent).toBe('Still visible');
    expect(container.querySelectorAll('.katex')).toHaveLength(1);
  });

  it('handles incomplete inline streaming and reparses when the delimiter arrives', () => {
    const { container, rerender } = render(message('Start $\\frac{a}'));
    expect(container.querySelector('.katex')).toBeNull();
    expect(container.textContent).toBe('Start $\\frac{a}');
    rerender(message('Start $\\frac{a}{b}$ done'));
    expect(container.querySelector('annotation')?.textContent).toBe('\\frac{a}{b}');
    expect(container.textContent).toContain(' done');
  });

  it('handles an open block at EOF, invalid partial tokens and completed streaming', () => {
    const { container, rerender } = render(message('$$\n\\frac{'));
    expect(container.querySelector('.katex-error')).toBeTruthy();
    rerender(message('$$\n\\frac{a}{b}'));
    expect(container.querySelector('.katex-display math')).toBeTruthy();
    rerender(message('$$\n\\frac{a}{b}\n$$\n\nFinished'));
    expect(container.querySelectorAll('.katex')).toHaveLength(1);
    expect(container.textContent).toContain('Finished');
  });

  it('uses explicit escaped dollars for currency and leaves a lone currency delimiter literal', () => {
    const { container } = render(message('Cost \\$5 and \\$10; lone $20.'));
    expect(container.querySelector('.katex')).toBeNull();
    expect(container.textContent).toBe('Cost $5 and $10; lone $20.');
  });

  it('uses remark-math syntax for paired dollar numbers without currency heuristics', () => {
    const { container } = render(message('$5$'));
    expect(container.querySelector('annotation')?.textContent).toBe('5');
  });

  it('retains links, attachments, GFM, and search marks alongside formulas', () => {
    const { container } = render(message('needle $x^2$ **bold** [site](https://example.test) [file](/api/attachments/ref/att_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa?session_id=s1)\n\n| A | B |\n| - | - |\n| needle | ok |', 'needle'));
    expect(container.querySelectorAll('mark')).toHaveLength(2);
    expect(container.querySelector('mark.history-search-word-active')?.textContent).toBe('needle');
    expect(container.querySelector('math')).toBeTruthy();
    expect(container.querySelector('strong')?.textContent).toBe('bold');
    expect(container.querySelector('a')?.getAttribute('href')).toBe('https://example.test');
    expect(container.querySelector('[data-testid="draggable-attachment"]')).toBeTruthy();
    expect(container.querySelectorAll('td')).toHaveLength(2);
  });

  it('keeps untrusted KaTeX commands and raw HTML inert', () => {
    const { container } = render(message('$\\href{javascript:alert(1)}{click}$\n\n$\\htmlClass{owned}{x}$\n\n<script>alert(1)</script>'));
    expect(container.querySelector('a, script, .owned')).toBeNull();
    expect(container.textContent).toContain('\\href');
    expect(container.textContent).toContain('\\htmlClass');
  });
});
