import type { ApiSessionHistoryResponse, Message } from '@/types';

export const SESSION_SEARCH_PAGE_SIZE = 200;
const SNIPPET_RADIUS = 42;

export interface SessionSearchHit {
  messageId: string;
  messageIndex: number;
  role: 'user' | 'assistant';
  snippet: string;
}

export interface HistoryVersion {
  total?: number | null;
  historyEpoch?: string | null;
  historyRevision?: number;
}

export interface SessionSearchScan {
  hits: SessionSearchHit[];
  total: number;
  historyEpoch?: string;
  historyRevision?: number;
}

export type HistoryPageLoader = (
  sessionId: string,
  before: number,
  limit: number,
  signal?: AbortSignal,
) => Promise<ApiSessionHistoryResponse>;

export class StaleSessionHistoryError extends Error {
  constructor() {
    super('Session history changed while it was being searched');
    this.name = 'StaleSessionHistoryError';
  }
}

function assertVersionMatches(actual: HistoryVersion, expected: HistoryVersion): void {
  if (typeof expected.total === 'number' && actual.total !== expected.total) {
    throw new StaleSessionHistoryError();
  }
  if (
    expected.historyEpoch != null && actual.historyEpoch != null &&
    expected.historyEpoch !== actual.historyEpoch
  ) {
    throw new StaleSessionHistoryError();
  }
  if (
    typeof expected.historyRevision === 'number' &&
    typeof actual.historyRevision === 'number' &&
    expected.historyRevision !== actual.historyRevision
  ) {
    throw new StaleSessionHistoryError();
  }
}

function makeSnippet(content: string, index: number, queryLength: number): string {
  const start = Math.max(0, index - SNIPPET_RADIUS);
  const end = Math.min(content.length, index + queryLength + SNIPPET_RADIUS);
  const excerpt = content.slice(start, end).replace(/\s+/g, ' ').trim();
  return `${start > 0 ? '…' : ''}${excerpt}${end < content.length ? '…' : ''}`;
}

/**
 * Scan the current Session's paged history and retain only matching references
 * and excerpts. The API's `legacy:` compatibility IDs are intentionally not
 * treated as durable identities and cannot be returned as search results.
 */
export async function scanSessionHistory(
  loadPage: HistoryPageLoader,
  sessionId: string,
  rawQuery: string,
  options: { signal?: AbortSignal; expectedVersion?: HistoryVersion; pageSize?: number } = {},
): Promise<SessionSearchScan> {
  const query = rawQuery.trim();
  const needle = query.toLowerCase();
  if (!needle) return { hits: [], total: 0 };

  const { signal, expectedVersion, pageSize = SESSION_SEARCH_PAGE_SIZE } = options;
  const ids = new Set<string>();
  const hits: SessionSearchHit[] = [];
  let before = 0;
  let version: HistoryVersion | null = null;
  let exhausted = false;

  while (!exhausted) {
    if (signal?.aborted) throw new DOMException('Search aborted', 'AbortError');
    const page = await loadPage(sessionId, before, pageSize, signal);
    if (signal?.aborted) throw new DOMException('Search aborted', 'AbortError');

    const pageVersion: HistoryVersion = {
      total: page.total,
      historyEpoch: page.historyEpoch,
      historyRevision: page.historyRevision,
    };
    if (!version) {
      version = pageVersion;
      if (expectedVersion) assertVersionMatches(pageVersion, expectedVersion);
    } else {
      assertVersionMatches(pageVersion, version);
    }

    const messages = page.history ?? [];
    messages.forEach((message: Message, localIndex) => {
      if (message.role !== 'user' && message.role !== 'assistant') return;
      const messageId = message.messageId;
      if (!messageId || messageId.startsWith('legacy:') || ids.has(messageId)) return;

      const at = (message.content ?? '').toLowerCase().indexOf(needle);
      if (at < 0) return;
      ids.add(messageId);
      hits.push({
        messageId,
        messageIndex: page.start + localIndex,
        role: message.role,
        snippet: makeSnippet(message.content, at, query.length),
      });
    });

    if (page.start <= 0) {
      exhausted = true;
      continue;
    }
    if (page.start >= before && before > 0) throw new StaleSessionHistoryError();
    if (messages.length === 0) throw new StaleSessionHistoryError();
    before = page.start;
  }

  hits.sort((a, b) => a.messageIndex - b.messageIndex);
  return {
    hits,
    total: version?.total ?? 0,
    ...(version?.historyEpoch != null ? { historyEpoch: version.historyEpoch } : {}),
    ...(typeof version?.historyRevision === 'number'
      ? { historyRevision: version.historyRevision }
      : {}),
  };
}
