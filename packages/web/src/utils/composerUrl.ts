/**
 * Inline URL nodes for the chat composer.
 *
 * A URL pasted into the composer becomes an atomic node, like an inline
 * attachment, but it owns no server resource: the address itself is the whole
 * value.  That difference matters for every layer above the DOM:
 *
 *  - the draft persists the node as an ordered part, not as rendered text;
 *  - the outbound message is a standard Markdown link, so the agent reads the
 *    real URL and a message bubble can render it as a link;
 *  - drag/copy carries URL + label, never bytes and never a server id.
 *
 * Only `http:`/`https:` become nodes.  Everything else (javascript:, data:,
 * file:, relative links, malformed text) stays plain text: a paste must never
 * be able to turn clipboard content into an executable link.
 */

import { escapeMarkdownLabel } from '@/utils/attachmentMarkdown';

export interface ComposerUrlPart {
  type: 'url';
  /** Local occurrence identity.  Not a server id; used for DOM/model mapping. */
  id: string;
  url: string;
  /** Visible title from HTML clipboard data.  Absent when the URL is its own label. */
  label?: string;
}

export interface ComposerTextPart {
  type: 'text';
  value: string;
}

/** Parsed, id-less part; the composer assigns local identities on insertion. */
export type ParsedComposerPart = ComposerTextPart | Omit<ComposerUrlPart, 'id'>;

const SAFE_PROTOCOL = /^https?:\/\//i;
const UNSAFE_URL_CHARACTERS = /[\u0000-\u0020\u007f<>"'`]/;
// Do not treat sentence punctuation as part of the address.  `?` and `#` are
// deliberately absent: a query/hash is part of the URL and must never be
// rewritten or truncated.
const TRAILING_PUNCTUATION = /[.,;:!，。；：！？、）】》]+$/u;
const URL_PATTERN = /https?:\/\/[^\s<>"'`]+/giu;
// A standard Markdown link whose destination is http(s).  This is the exact
// shape `composerUrlMarkdown` writes, so a copied node can be pasted back from
// the plain-text clipboard path with its label intact.
const MARKDOWN_LINK_PATTERN =
  /\[((?:\\.|[^\]\\])*)\]\(((?:https?:\/\/)(?:\\.|[^\s()])+)\)/giu;

export const COMPOSER_URL_MIME = 'application/x-pan-composer-url';

/** Structural subset shared by DataTransfer and ClipboardEvent.clipboardData. */
export interface ClipboardDataLike {
  getData: (type: string) => string;
  setData: (type: string, value: string) => void;
}

export interface ComposerUrlTransfer {
  items: Array<{ url: string; label?: string }>;
  /** Present only for a drag inside a composer; paste/copy clears it. */
  sourceId?: string;
}

export function newComposerUrlId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `url_${crypto.randomUUID()}`;
  }
  return `url_${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/** Return the original address when it is a safe absolute http(s) URL. */
export function normalizeComposerUrl(raw: string): string | null {
  const value = raw.trim();
  if (!value || !SAFE_PROTOCOL.test(value)) return null;
  if (UNSAFE_URL_CHARACTERS.test(value)) return null;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    if (!parsed.hostname) return null;
    // Return the pasted bytes, not `parsed.toString()`: query, hash, percent
    // escapes and case must survive exactly as the user copied them.
    return value;
  } catch {
    return null;
  }
}

/** Escape a Markdown link destination without re-encoding the address. */
export function escapeMarkdownUrlDestination(url: string): string {
  return url.replace(/[\\()]/g, '\\$&');
}

export function composerUrlMarkdown(url: string, label?: string): string {
  const text = label?.trim() || url;
  return `[${escapeMarkdownLabel(text)}](${escapeMarkdownUrlDestination(url)})`;
}

function urlLabel(url: string, label: string | undefined): string | undefined {
  const trimmed = label?.replace(/\s+/g, ' ').trim();
  return trimmed && trimmed !== url ? trimmed : undefined;
}

function trimUrlCandidate(raw: string): string {
  let value = raw.replace(TRAILING_PUNCTUATION, '');
  // A closing paren that has no opening partner belongs to the surrounding
  // prose, not to the address.  Balanced parens stay untouched.
  while (value.endsWith(')')) {
    const opens = (value.match(/\(/g) || []).length;
    const closes = (value.match(/\)/g) || []).length;
    if (closes <= opens) break;
    value = value.slice(0, -1);
  }
  return value;
}

function unescapeMarkdownLabel(value: string): string {
  return value.replace(/\\([\\[\]()])/g, '$1');
}

function unescapeMarkdownDestination(value: string): string {
  return value.replace(/\\([\\()])/g, '$1');
}

/** Parse raw http(s) URLs inside a text run with no Markdown links in it. */
function parseRawUrlRun(text: string): ParsedComposerPart[] {
  const parts: ParsedComposerPart[] = [];
  let cursor = 0;
  for (const match of text.matchAll(URL_PATTERN)) {
    const start = match.index ?? 0;
    const raw = trimUrlCandidate(match[0]);
    const url = normalizeComposerUrl(raw);
    if (!url) continue;
    if (start > cursor) parts.push({ type: 'text', value: text.slice(cursor, start) });
    parts.push({ type: 'url', url, label: urlLabel(url, url) });
    cursor = start + raw.length;
  }
  if (parts.length === 0) return [{ type: 'text', value: text }];
  if (cursor < text.length) parts.push({ type: 'text', value: text.slice(cursor) });
  return parts;
}

/**
 * Split pasted plain text into ordered text/url parts.
 *
 * Standard Markdown links are recognized first: they are what a copied URL
 * node puts on the plain-text clipboard, so pasting that text (in Pan or after
 * an external round trip that drops the custom MIME) recreates the node with
 * its visible title.  Remaining spans are scanned for raw http(s) URLs.
 * Every accepted URL keeps its original bytes.
 */
export function parseComposerText(text: string): ParsedComposerPart[] {
  const parts: ParsedComposerPart[] = [];
  let cursor = 0;
  for (const match of text.matchAll(MARKDOWN_LINK_PATTERN)) {
    const start = match.index ?? 0;
    const url = normalizeComposerUrl(unescapeMarkdownDestination(match[2] ?? ''));
    if (!url) continue;
    if (start > cursor) parts.push(...parseRawUrlRun(text.slice(cursor, start)));
    parts.push({
      type: 'url',
      url,
      label: urlLabel(url, unescapeMarkdownLabel(match[1] ?? '')),
    });
    cursor = start + (match[0] ?? '').length;
  }
  if (cursor < text.length) parts.push(...parseRawUrlRun(text.slice(cursor)));
  if (parts.length === 0) return [{ type: 'text', value: text }];
  return mergeAdjacentText(parts);
}

const BLOCK_TAGS = new Set([
  'ADDRESS',
  'ARTICLE',
  'ASIDE',
  'BLOCKQUOTE',
  'DD',
  'DIV',
  'DL',
  'DT',
  'FIELDSET',
  'FIGCAPTION',
  'FIGURE',
  'FOOTER',
  'FORM',
  'H1',
  'H2',
  'H3',
  'H4',
  'H5',
  'H6',
  'HEADER',
  'HR',
  'LI',
  'MAIN',
  'NAV',
  'OL',
  'P',
  'PRE',
  'SECTION',
  'TABLE',
  'TBODY',
  'TD',
  'TH',
  'THEAD',
  'TR',
  'UL',
]);

function mergeAdjacentText(parts: ParsedComposerPart[]): ParsedComposerPart[] {
  const merged: ParsedComposerPart[] = [];
  for (const part of parts) {
    if (part.type === 'text' && part.value === '') continue;
    const previous = merged.at(-1);
    if (part.type === 'text' && previous?.type === 'text') {
      previous.value += part.value;
    } else {
      merged.push(part.type === 'text' ? { ...part } : { ...part });
    }
  }
  return merged;
}

function isHidden(element: HTMLElement): boolean {
  const style = element.getAttribute('style') || '';
  return (
    element.hidden ||
    (element.tagName === 'INPUT' && element.getAttribute('type') === 'hidden') ||
    element.getAttribute('aria-hidden') === 'true' ||
    /(?:^|[;\s])(display\s*:\s*none|visibility\s*:\s*hidden)/i.test(style)
  );
}

/**
 * Normalize whitespace across the whole part sequence.
 *
 * Trimming each text run independently would delete the line break that a
 * block boundary puts *before* a link, turning two lines into one.  Only the
 * sequence's own leading/trailing newlines are document padding.
 */
function normalizeTextSequence(parts: ParsedComposerPart[]): ParsedComposerPart[] {
  const result = parts.map((part) => {
    if (part.type !== 'text') return part;
    return {
      ...part,
      value: part.value
        .replace(/\u00a0/g, ' ')
        .replace(/[ \t]+\n/g, '\n')
        .replace(/\n[ \t]+/g, '\n')
        .replace(/\n{3,}/g, '\n\n'),
    };
  });
  const first = result[0];
  if (first?.type === 'text') first.value = first.value.replace(/^\n+/, '');
  const last = result.at(-1);
  if (last?.type === 'text') last.value = last.value.replace(/\n+$/, '');
  return result.filter((part) => part.type !== 'text' || part.value !== '');
}

/**
 * Parse clipboard HTML into ordered parts, preserving anchor href + visible
 * title.  Returns null when the fragment contains no safe link, so callers can
 * keep the existing sanitize-to-text path (and its block/blank-line rules).
 */
export function parseComposerHtml(html: string): ParsedComposerPart[] | null {
  if (!html || typeof DOMParser === 'undefined') return null;
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  parsed.querySelectorAll('script,style,template,noscript').forEach((node) => node.remove());
  parsed.querySelectorAll<HTMLElement>('*').forEach((element) => {
    if (isHidden(element)) element.remove();
  });
  const anchors = Array.from(parsed.body.querySelectorAll('a[href]'));
  const safeAnchors = anchors
    .map((anchor) => ({
      anchor,
      url: normalizeComposerUrl(anchor.getAttribute('href') || ''),
    }))
    .filter((entry): entry is { anchor: HTMLAnchorElement; url: string } => !!entry.url);
  if (safeAnchors.length === 0) return null;
  const safe = new Set(safeAnchors.map((entry) => entry.anchor));

  const parts: ParsedComposerPart[] = [];
  const pushText = (value: string) => {
    if (!value) return;
    const previous = parts.at(-1);
    if (previous?.type === 'text') previous.value += value;
    else parts.push({ type: 'text', value });
  };

  const visit = (node: Node): void => {
    if (node.nodeType === Node.TEXT_NODE) {
      pushText(node.textContent || '');
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const element = node as HTMLElement;
    if (element.tagName === 'BR') {
      pushText('\n');
      return;
    }
    if (element.tagName === 'A' && safe.has(element as HTMLAnchorElement)) {
      const url = normalizeComposerUrl(element.getAttribute('href') || '');
      if (url) {
        parts.push({
          type: 'url',
          url,
          label: urlLabel(url, element.textContent || ''),
        });
        return;
      }
    }
    const block = BLOCK_TAGS.has(element.tagName);
    if (block) pushText('\n');
    Array.from(element.childNodes).forEach(visit);
    if (block) pushText('\n');
  };

  Array.from(parsed.body.childNodes).forEach(visit);
  const normalized = normalizeTextSequence(mergeAdjacentText(parts));
  return normalized.length > 0 ? normalized : null;
}

/** Build the drag/copy transfer for one or more URL nodes. */
export function writeComposerUrlTransfer(
  dataTransfer: ClipboardDataLike | null,
  transfer: ComposerUrlTransfer,
): void {
  if (!dataTransfer) return;
  const payload: ComposerUrlTransfer = {
    items: transfer.items
      .map((item) => {
        const url = normalizeComposerUrl(item.url);
        if (!url) return null;
        const label = urlLabel(url, item.label);
        return label ? { url, label } : { url };
      })
      .filter((item): item is { url: string; label?: string } => !!item),
    ...(transfer.sourceId ? { sourceId: transfer.sourceId } : {}),
  };
  if (payload.items.length === 0) return;
  dataTransfer.setData(COMPOSER_URL_MIME, JSON.stringify(payload));
}

export function readComposerUrlTransfer(
  dataTransfer: ClipboardDataLike | null,
): ComposerUrlTransfer | null {
  if (!dataTransfer) return null;
  let raw = '';
  try {
    raw = dataTransfer.getData(COMPOSER_URL_MIME);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    const record = parsed as Record<string, unknown>;
    if (!Array.isArray(record.items)) return null;
    const items: Array<{ url: string; label?: string }> = [];
    for (const value of record.items) {
      if (!value || typeof value !== 'object') continue;
      const item = value as Record<string, unknown>;
      if (typeof item.url !== 'string') continue;
      const url = normalizeComposerUrl(item.url);
      if (!url) continue;
      const label = typeof item.label === 'string' ? urlLabel(url, item.label) : undefined;
      items.push(label ? { url, label } : { url });
    }
    if (items.length === 0) return null;
    const sourceId =
      typeof record.sourceId === 'string' && record.sourceId ? record.sourceId : undefined;
    return { items, ...(sourceId ? { sourceId } : {}) };
  } catch {
    return null;
  }
}
