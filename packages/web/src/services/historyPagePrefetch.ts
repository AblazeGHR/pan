import { fetchSessionHistory } from './api';
import type { ApiSessionHistoryResponse } from '@/types';

export interface HistoryPrefetchContext {
  sessionId: string;
  selection: number;
  serverEpoch: string | null;
  historyEpoch: string;
  historyRevision: number;
  limit: number;
}
const MAX_PAGES = 3;
const MAX_CHARACTERS = 500_000;
const TTL_MS = 60_000;
let contextKey = '';
let pending: { controller: AbortController; key: string } | null = null;
const pages = new Map<number, { page: ApiSessionHistoryResponse; chars: number; at: number }>();

function key(context: HistoryPrefetchContext): string {
  return JSON.stringify(context);
}
export function cancelHistoryPrefetch(): void {
  pending?.controller.abort();
  pending = null;
}
export function syncHistoryPrefetch(context: HistoryPrefetchContext | null): void {
  const next = context ? key(context) : '';
  if (next === contextKey) return;
  cancelHistoryPrefetch();
  contextKey = next;
  pages.clear();
}
function prune(): void {
  for (const [before, entry] of pages) {
    if (Date.now() - entry.at >= TTL_MS) pages.delete(before);
  }
}
export function peekHistoryPrefetch(
  context: HistoryPrefetchContext | null,
  before: number,
): boolean {
  prune();
  return !!context && key(context) === contextKey && pages.has(before);
}
export function takeHistoryPrefetch(
  context: HistoryPrefetchContext | null,
  before: number,
): ApiSessionHistoryResponse | null {
  if (!peekHistoryPrefetch(context, before)) return null;
  const entry = pages.get(before)!;
  pages.delete(before);
  return entry.page;
}
// Follow a contiguous chain without adding rows to any Session/store/DOM.
export function nextHistoryPrefetchBefore(
  context: HistoryPrefetchContext,
  before: number,
): number | null {
  if (key(context) !== contextKey || pending) return null;
  prune();
  // Search/navigation can load past several prefetched pages at once.
  // Already-covered pages must not consume the entire speculation budget.
  for (const offset of pages.keys()) if (offset > before) pages.delete(offset);
  let cursor = before;
  while (pages.has(cursor)) cursor = pages.get(cursor)!.page.start!;
  if (cursor <= 0 || pages.size >= MAX_PAGES) return null;
  return cursor;
}
export async function prefetchHistoryPage(
  context: HistoryPrefetchContext,
  before: number,
): Promise<boolean> {
  if (pending || key(context) !== contextKey || pages.size >= MAX_PAGES) return false;
  const task = { controller: new AbortController(), key: contextKey };
  pending = task;
  try {
    const page = await fetchSessionHistory(
      context.sessionId,
      before,
      context.limit,
      task.controller.signal,
      false,
      true,
    );
    if (task.controller.signal.aborted || task !== pending || task.key !== contextKey) return false;
    if (
      page.historyEpoch !== context.historyEpoch ||
      page.historyRevision !== context.historyRevision ||
      page.start !== Math.max(0, before - context.limit) ||
      typeof page.total !== 'number' ||
      page.total < before ||
      !Array.isArray(page.history) ||
      page.history.length !== before - page.start
    )
      return false;
    const chars = JSON.stringify(page).length;
    const retained = [...pages.values()].reduce((sum, item) => sum + item.chars, 0);
    if (retained + chars > MAX_CHARACTERS) return false;
    pages.set(before, { page, chars, at: Date.now() });
    return true;
  } catch {
    // Speculation is optional. An abort/error leaves the normal reader intact.
    return false;
  } finally {
    if (pending === task) pending = null;
  }
}
