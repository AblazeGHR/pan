// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { HistorySearchOverview, SearchSnippet } from './HistorySearchOverview';
import type { ApiHistorySearchHit } from '@/types';

afterEach(cleanup);
it('highlights repeated literal terms safely, including Unicode expansion and markup-looking text', () => {
  const view = render(<SearchSnippet text={'<script> Straße STRASSE 中文 中文 !?'} query="strasse" />);
  expect([...view.container.querySelectorAll('mark')].map((mark) => mark.textContent)).toEqual(['Straße', 'STRASSE']);
  expect(view.container.querySelector('script')).toBeNull();
  view.rerender(<SearchSnippet text="中文 中文 !?" query="中" />);
  expect(view.container.querySelectorAll('mark')).toHaveLength(2);
  view.rerender(<SearchSnippet text="!? !?" query="!?" />);
  expect(view.container.querySelectorAll('mark')).toHaveLength(2);
});
it('shares foldable rows with optional Session titles and keeps paging inside the folded region', () => {
  const hit = { sessionId: 's', messageId: 'm', role: 'tool', snippet: 'needle needle', matchCount: 2 } as ApiHistorySearchHit;
  const select = vi.fn();
  const view = render(<HistorySearchOverview hits={[hit]} query="needle" onSelect={select}><button>Load more</button></HistorySearchOverview>);
  expect(view.container.querySelector('.global-history-search__session-name')).toBeNull();
  expect(view.container.querySelectorAll('mark')).toHaveLength(2);
  fireEvent.click(view.getByRole('button', { name: /tool, needle needle/ }));
  expect(select).toHaveBeenCalledWith(hit);
  fireEvent.click(view.getByRole('button', { name: 'Collapse results' }));
  expect(view.queryByRole('list')).toBeNull();
  expect(view.queryByRole('button', { name: 'Load more' })).toBeNull();
  fireEvent.click(view.getByRole('button', { name: 'Expand results' }));
  expect(view.getByRole('list')).toBeTruthy();
  view.rerender(<HistorySearchOverview hits={[hit]} query="needle" sessionName={() => 'Title'} onSelect={select} />);
  expect(view.getByText('Title')).toBeTruthy();
});
