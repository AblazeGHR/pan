// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { MarkdownRenderer } from './MarkdownRenderer';

describe('MarkdownRenderer', () => {
  it('renders list bullets structure and hljs spans', () => {
    const md = [
      '- item one',
      '- item two',
      '',
      '```js',
      'const x = 1;',
      '```',
    ].join('\n');
    const { container } = render(<MarkdownRenderer content={md} />);
    const ul = container.querySelector('ul');
    const li = container.querySelector('li');
    const hljsKeyword = container.querySelector('.hljs-keyword');
    const codeEl = container.querySelector('code.hljs');
    console.log('UL:', ul ? ul.outerHTML.slice(0, 300) : 'none');
    console.log('LI:', li ? li.outerHTML.slice(0, 120) : 'none');
    console.log('HLJS_KEYWORD:', hljsKeyword ? hljsKeyword.outerHTML : 'none');
    console.log('CODE:', codeEl ? codeEl.outerHTML.slice(0, 300) : 'none');
    expect(ul).toBeTruthy();
    expect(li).toBeTruthy();
    expect(hljsKeyword).toBeTruthy();
  });
});
