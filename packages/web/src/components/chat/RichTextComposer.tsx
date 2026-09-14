import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type ClipboardEvent as ReactClipboardEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import {
  readAttachmentDragPayload,
  writeAttachmentDragPayload,
  type AttachmentDragPayload,
} from '@/utils/attachmentDrag';
import { isSafeAttachmentHref } from '@/utils/attachmentMarkdown';
import { hasPanAttachmentMime, inspectNativeAttachmentInput } from '@/utils/nativeAttachmentInput';

export type ComposerPart =
  | { type: 'text'; value: string }
  | { type: 'attachment'; attachmentId: string; occurrenceId?: string };

export interface ComposerValue {
  parts: ComposerPart[];
  text: string;
  /** Canonical local occurrence ids; one resource may occur more than once. */
  occurrenceIds: string[];
  /** Compatibility alias for older callers; these are occurrence ids, not server resource ids. */
  attachmentIds: string[];
}

export interface RichTextComposerHandle {
  replaceText: (text: string) => void;
  replaceValue: (value: ComposerValue) => void;
  focus: () => void;
}

interface RichTextComposerProps {
  initialText?: string;
  attachments: ComposerAttachment[];
  onChange: (value: ComposerValue) => void;
  onAttachmentDrop: (payload: AttachmentDragPayload) => string | null;
  /** Add browser File objects and return local composer ids synchronously. */
  onNativeFiles?: (files: File[], offset: number, source: 'paste' | 'drop') => string[];
  onNativeInputIssue?: (kind: 'directory' | 'uri' | 'invalid-pan-attachment') => void;
  onRemoveAttachment: (attachmentId: string) => void;
  onKeyDown?: (event: ReactKeyboardEvent<HTMLElement>) => void;
}

interface ComposerAttachment {
  /** Legacy prop name retained for existing callers. */
  id: string;
  /** Local occurrence identity; never the server attachment resource id. */
  occurrenceId?: string;
  /** Server resource identity, used only for documentation/diagnostics. */
  attachmentId?: string;
  displayName: string;
  href?: string;
  path?: string;
}

interface DropIndicator {
  left: number;
  top: number;
  height: number;
}

const EMPTY_PARTS: ComposerPart[] = [{ type: 'text', value: '' }];
const BLOCK_TAGS = new Set(['DIV', 'LI', 'P']);

function mergeTextParts(parts: ComposerPart[]): ComposerPart[] {
  const merged: ComposerPart[] = [];
  for (const part of parts) {
    if (part.type === 'text' && part.value === '') continue;
    const previous = merged.at(-1);
    if (part.type === 'text' && previous?.type === 'text') {
      previous.value += part.value;
    } else {
      merged.push({ ...part });
    }
  }
  return merged.length > 0 ? merged : EMPTY_PARTS;
}

function partOccurrenceId(part: ComposerPart): string | null {
  return part.type === 'attachment' ? part.occurrenceId || part.attachmentId : null;
}

function attachmentOccurrenceId(attachment: ComposerAttachment): string {
  return attachment.occurrenceId || attachment.id;
}

function attachmentPart(occurrenceId: string): Extract<ComposerPart, { type: 'attachment' }> {
  // Keep attachmentId as a compatibility alias while making occurrenceId
  // explicit.  Neither field is the server-owned resource attachmentId.
  return { type: 'attachment', occurrenceId, attachmentId: occurrenceId };
}

const SVG_NAMESPACE = 'http://www.w3.org/2000/svg';

function createIcon(size: number, className: string, paths: string[]): SVGSVGElement {
  const icon = document.createElementNS(SVG_NAMESPACE, 'svg');
  icon.setAttribute('xmlns', SVG_NAMESPACE);
  icon.setAttribute('width', String(size));
  icon.setAttribute('height', String(size));
  icon.setAttribute('viewBox', '0 0 24 24');
  icon.setAttribute('fill', 'none');
  icon.setAttribute('stroke', 'currentColor');
  icon.setAttribute('stroke-width', '2');
  icon.setAttribute('stroke-linecap', 'round');
  icon.setAttribute('stroke-linejoin', 'round');
  icon.setAttribute('class', className);
  icon.setAttribute('aria-hidden', 'true');
  for (const d of paths) {
    const path = document.createElementNS(SVG_NAMESPACE, 'path');
    path.setAttribute('d', d);
    icon.append(path);
  }
  return icon;
}

function createAttachmentNode(attachment: ComposerAttachment): HTMLSpanElement {
  const node = document.createElement('span');
  const occurrenceId = attachmentOccurrenceId(attachment);
  node.dataset.composerAttachment = occurrenceId;
  node.contentEditable = 'false';
  node.draggable = !!attachment.href && isSafeAttachmentHref(attachment.href);
  node.setAttribute('role', 'group');
  node.setAttribute('aria-label', `附件 ${attachment.displayName}`);
  node.className =
    'composer-attachment-node mx-0.5 inline-flex max-w-full select-none items-center gap-1 rounded border border-accent/50 bg-accent/10 px-1.5 py-0.5 align-baseline text-xs text-accent';

  const label = document.createElement('span');
  label.dataset.composerAttachmentName = '';
  label.className = 'max-w-[14rem] truncate';
  label.textContent = attachment.displayName;

  const removeButton = document.createElement('button');
  removeButton.type = 'button';
  removeButton.dataset.composerAttachmentDelete = occurrenceId;
  removeButton.setAttribute('aria-label', `删除附件 ${attachment.displayName}`);
  removeButton.className =
    'ml-0.5 shrink-0 rounded p-0.5 text-accent/80 hover:bg-accent/20 hover:text-accent';

  node.append(
    createIcon(13, 'shrink-0', [
      'M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z',
      'M14 2v5a1 1 0 0 0 1 1h5',
    ]),
    label,
    removeButton,
  );
  removeButton.append(createIcon(12, '', ['M18 6 6 18', 'm6 6 12 12']));
  return node;
}

/**
 * React must not reconcile children inside a live contentEditable. Chromium
 * can move a typed text node outside the JSX wrapper that was rendered for
 * it; reconciling that stale tree later would append the same text a second
 * time. Keep the editor host uncontrolled and render only structural updates
 * through this DOM-owned boundary.
 */
function renderParts(
  root: HTMLElement,
  parts: ComposerPart[],
  attachments: ComposerAttachment[],
): void {
  const attachmentById = new Map(
    attachments.map((attachment) => [attachmentOccurrenceId(attachment), attachment]),
  );
  const fragment = document.createDocumentFragment();
  const normalized = mergeTextParts(parts);
  for (const part of normalized) {
    if (part.type === 'text') {
      const text = document.createElement('span');
      text.textContent = part.value;
      fragment.append(text);
      continue;
    }
    const attachmentId = partOccurrenceId(part);
    const attachment = attachmentId ? attachmentById.get(attachmentId) : undefined;
    if (attachment) fragment.append(createAttachmentNode(attachment));
  }
  if (!fragment.childNodes.length) {
    const emptyText = document.createElement('span');
    fragment.append(emptyText);
  }
  root.replaceChildren(fragment);
}

function syncAttachmentNodes(root: HTMLElement, attachments: ComposerAttachment[]): void {
  const attachmentById = new Map(
    attachments.map((attachment) => [attachmentOccurrenceId(attachment), attachment]),
  );
  root.querySelectorAll<HTMLElement>('[data-composer-attachment]').forEach((node) => {
    const id = node.dataset.composerAttachment;
    const attachment = id ? attachmentById.get(id) : undefined;
    if (!attachment) return;
    node.draggable = !!attachment.href && isSafeAttachmentHref(attachment.href);
    node.setAttribute('aria-label', `附件 ${attachment.displayName}`);
    const label = node.querySelector<HTMLElement>('[data-composer-attachment-name]');
    if (label) label.textContent = attachment.displayName;
    const removeButton = node.querySelector<HTMLButtonElement>('[data-composer-attachment-delete]');
    if (removeButton) {
      removeButton.dataset.composerAttachmentDelete = attachmentOccurrenceId(attachment);
      removeButton.setAttribute('aria-label', `删除附件 ${attachment.displayName}`);
    }
  });
}

function partLength(part: ComposerPart): number {
  return part.type === 'text' ? part.value.length : 1;
}

function attachmentOffset(parts: ComposerPart[], attachmentId: string): number | null {
  let offset = 0;
  for (const part of parts) {
    if (partOccurrenceId(part) === attachmentId) return offset;
    offset += partLength(part);
  }
  return null;
}

function valueFromParts(parts: ComposerPart[]): ComposerValue {
  const normalized = mergeTextParts(parts);
  const occurrenceIds = normalized.map(partOccurrenceId).filter((id): id is string => !!id);
  return {
    parts: normalized,
    text: normalized
      .filter((part): part is Extract<ComposerPart, { type: 'text' }> => part.type === 'text')
      .map((part) => part.value)
      .join(''),
    occurrenceIds,
    attachmentIds: occurrenceIds,
  };
}

function readParts(root: HTMLElement): ComposerPart[] {
  const parts: ComposerPart[] = [];
  const appendLineBreak = (force = false) => {
    const last = parts.at(-1);
    if (!force && last?.type === 'text' && last.value.endsWith('\n')) return;
    parts.push({ type: 'text', value: '\n' });
  };

  interface SequenceResult {
    hasContent: boolean;
    endsWithNewline: boolean;
  }

  const processSequence = (nodes: Node[], container: HTMLElement | null): SequenceResult => {
    let hasNode = false;
    let hasContent = false;
    let endsWithNewline = false;
    let previousWasBlock = false;
    let previousBlockEmpty = false;
    let previousBlockEndedWithNewline = false;

    for (const node of nodes) {
      const element = node.nodeType === Node.ELEMENT_NODE ? (node as HTMLElement) : null;
      const isBlock = !!element && BLOCK_TAGS.has(element.tagName);
      if (hasNode) {
        if (previousWasBlock) {
          // An empty block is a real blank line. Preserve the second
          // separator in <div>one</div><div><br></div><div>three</div>,
          // while avoiding an extra separator after an explicit trailing BR.
          appendLineBreak(previousBlockEmpty || !previousBlockEndedWithNewline);
        } else if (isBlock && hasContent && !endsWithNewline) {
          appendLineBreak();
        }
      }

      let result: SequenceResult;
      if (isBlock) {
        result = processSequence(Array.from(element.childNodes), element);
      } else if (node.nodeType === Node.TEXT_NODE) {
        const value = node.textContent || '';
        parts.push({ type: 'text', value });
        result = { hasContent: value.length > 0, endsWithNewline: value.endsWith('\n') };
      } else if (element?.dataset.composerAttachment) {
        parts.push(attachmentPart(element.dataset.composerAttachment));
        result = { hasContent: true, endsWithNewline: false };
      } else if (element?.tagName === 'BR') {
        const isPlaceholder =
          !!container &&
          BLOCK_TAGS.has(container.tagName) &&
          container.childNodes.length === 1 &&
          container.firstChild === element;
        if (!isPlaceholder) {
          parts.push({ type: 'text', value: '\n' });
          result = { hasContent: true, endsWithNewline: true };
        } else {
          result = { hasContent: false, endsWithNewline: false };
        }
      } else if (element) {
        result = processSequence(Array.from(element.childNodes), element);
      } else {
        result = { hasContent: false, endsWithNewline: false };
      }

      hasNode = true;
      hasContent = hasContent || result.hasContent;
      endsWithNewline = result.endsWithNewline;
      previousWasBlock = isBlock;
      previousBlockEmpty = isBlock && !result.hasContent;
      previousBlockEndedWithNewline = isBlock && result.endsWithNewline;
    }

    return { hasContent, endsWithNewline };
  };

  processSequence(Array.from(root.childNodes), root);
  return mergeTextParts(parts);
}

function removeAttachment(parts: ComposerPart[], attachmentId: string): ComposerPart[] {
  return mergeTextParts(parts.filter((part) => partOccurrenceId(part) !== attachmentId));
}

function insertAttachment(
  parts: ComposerPart[],
  offset: number,
  attachmentId: string,
): ComposerPart[] {
  const result: ComposerPart[] = [];
  let remaining = Math.max(0, offset);
  let inserted = false;

  for (const part of parts) {
    const length = partLength(part);
    if (!inserted && remaining <= length) {
      if (part.type === 'text') {
        result.push({ type: 'text', value: part.value.slice(0, remaining) });
        result.push(attachmentPart(attachmentId));
        result.push({ type: 'text', value: part.value.slice(remaining) });
      } else if (remaining === 0) {
        result.push(attachmentPart(attachmentId), part);
      } else {
        result.push(part, attachmentPart(attachmentId));
      }
      inserted = true;
      continue;
    }
    result.push(part);
    remaining -= length;
  }

  if (!inserted) result.push(attachmentPart(attachmentId));
  return mergeTextParts(result);
}

function insertParts(
  parts: ComposerPart[],
  offset: number,
  insertedParts: ComposerPart[],
): ComposerPart[] {
  let next = parts;
  let insertionOffset = Math.max(0, offset);
  for (const part of insertedParts) {
    if (part.type === 'text' && part.value === '') continue;
    if (part.type === 'attachment') {
      next = insertAttachment(next, insertionOffset, partOccurrenceId(part)!);
    } else {
      const result: ComposerPart[] = [];
      let remaining = insertionOffset;
      let inserted = false;
      for (const candidate of next) {
        const length = partLength(candidate);
        if (!inserted && remaining <= length) {
          if (candidate.type === 'text') {
            result.push({ type: 'text', value: candidate.value.slice(0, remaining) });
            result.push({ type: 'text', value: part.value });
            result.push({ type: 'text', value: candidate.value.slice(remaining) });
          } else if (remaining === 0) {
            result.push({ type: 'text', value: part.value }, candidate);
          } else {
            result.push(candidate, { type: 'text', value: part.value });
          }
          inserted = true;
          continue;
        }
        result.push(candidate);
        remaining -= length;
      }
      if (!inserted) result.push({ type: 'text', value: part.value });
      next = mergeTextParts(result);
    }
    insertionOffset += partLength(part);
  }
  return next;
}

function replaceRange(
  parts: ComposerPart[],
  start: number,
  end: number,
  insertedParts: ComposerPart[],
): { parts: ComposerPart[]; removedOccurrenceIds: string[] } {
  const rangeStart = Math.max(0, Math.min(start, end));
  const rangeEnd = Math.max(rangeStart, end);
  const kept: ComposerPart[] = [];
  const removedOccurrenceIds: string[] = [];
  let cursor = 0;
  for (const part of parts) {
    const length = partLength(part);
    const partStart = cursor;
    const partEnd = cursor + length;
    if (partEnd <= rangeStart || partStart >= rangeEnd) {
      kept.push(part);
    } else if (part.type === 'text') {
      const left = Math.max(0, rangeStart - partStart);
      const right = Math.max(0, partEnd - rangeEnd);
      if (left > 0) kept.push({ type: 'text', value: part.value.slice(0, left) });
      if (right > 0)
        kept.push({ type: 'text', value: part.value.slice(part.value.length - right) });
    } else {
      const occurrenceId = partOccurrenceId(part);
      if (occurrenceId) removedOccurrenceIds.push(occurrenceId);
    }
    cursor = partEnd;
  }
  return {
    parts: insertParts(mergeTextParts(kept), rangeStart, insertedParts),
    removedOccurrenceIds,
  };
}

/** Translate a DOM Range boundary into the flat text/attachment coordinate space. */
function selectionOffset(root: HTMLElement, target: Node, offset: number): number | null {
  const cursor = { total: 0, hasContent: false, endsWithNewline: false, lastBlockEmpty: false };
  let found: number | null = null;

  const hasLogicalContent = (node: Node, container: HTMLElement | null): boolean => {
    if (node.nodeType === Node.TEXT_NODE) return (node.textContent || '').length > 0;
    if (node.nodeType !== Node.ELEMENT_NODE) return false;
    const element = node as HTMLElement;
    if (element.dataset.composerAttachment) return true;
    if (element.tagName === 'BR') {
      return !(
        container &&
        BLOCK_TAGS.has(container.tagName) &&
        container.childNodes.length === 1 &&
        container.firstChild === element
      );
    }
    return Array.from(element.childNodes).some((child) => hasLogicalContent(child, element));
  };

  const addBlockSeparator = (element: HTMLElement) => {
    if (
      BLOCK_TAGS.has(element.tagName) &&
      (cursor.lastBlockEmpty || (cursor.hasContent && !cursor.endsWithNewline))
    ) {
      cursor.total += 1;
      cursor.endsWithNewline = true;
    }
  };

  const consume = (node: Node, container: HTMLElement | null): boolean => {
    const element = node.nodeType === Node.ELEMENT_NODE ? (node as HTMLElement) : null;
    if (element?.dataset.composerAttachment) {
      cursor.total += 1;
      cursor.hasContent = true;
      cursor.endsWithNewline = false;
      return false;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      const value = node.textContent || '';
      if (value) {
        cursor.total += value.length;
        cursor.hasContent = true;
        cursor.endsWithNewline = value.endsWith('\n');
      }
      return false;
    }
    if (element?.tagName === 'BR') {
      if (
        container &&
        BLOCK_TAGS.has(container.tagName) &&
        container.childNodes.length === 1 &&
        container.firstChild === element
      ) {
        return false;
      }
      cursor.total += 1;
      cursor.hasContent = true;
      cursor.endsWithNewline = true;
      return false;
    }
    if (element) addBlockSeparator(element);
    for (const child of Array.from(node.childNodes)) {
      if (visit(child, element)) return true;
    }
    cursor.lastBlockEmpty =
      !!element && BLOCK_TAGS.has(element.tagName)
        ? !hasLogicalContent(element, element.parentElement)
        : false;
    return false;
  };

  function visit(node: Node, container: HTMLElement | null): boolean {
    if (found !== null) return true;
    if (node === target) {
      if (node.nodeType === Node.TEXT_NODE) {
        found = cursor.total + Math.min(offset, node.textContent?.length || 0);
      } else {
        const element = node.nodeType === Node.ELEMENT_NODE ? (node as HTMLElement) : null;
        if (element?.dataset.composerAttachment) {
          found = cursor.total + Math.min(offset, 1);
        } else if (
          element?.tagName === 'BR' &&
          container &&
          BLOCK_TAGS.has(container.tagName) &&
          container.childNodes.length === 1 &&
          container.firstChild === element
        ) {
          found = cursor.total;
        } else {
          if (element) addBlockSeparator(element);
          const children = Array.from(node.childNodes);
          for (let index = 0; index < Math.min(offset, children.length); index += 1) {
            consume(children[index]!, element);
          }
          found = cursor.total;
        }
      }
      return true;
    }
    return consume(node, container);
  }

  // The root itself is a valid selection container, so start at its children.
  if (target === root) {
    const children = Array.from(root.childNodes);
    for (let index = 0; index < Math.min(offset, children.length); index += 1) {
      consume(children[index]!, root);
    }
    // A block wrapper contributes its line separator immediately before the
    // wrapper in readParts().  A root-level selection boundary before that
    // wrapper has not consumed the wrapper yet, so account for the same
    // separator here; otherwise dropping at the start of a later line lands
    // one character too early.
    const next = children[offset];
    if (next?.nodeType === Node.ELEMENT_NODE) addBlockSeparator(next as HTMLElement);
    return cursor.total;
  }
  root.childNodes.forEach((node) => visit(node, root));
  return found;
}

function selectedOffsets(root: HTMLElement): { start: number; end: number } | null {
  const selection = window.getSelection();
  if (!selection?.rangeCount) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const start = selectionOffset(root, range.startContainer, range.startOffset);
  const end = selectionOffset(root, range.endContainer, range.endOffset);
  if (start === null || end === null) return null;
  return start <= end ? { start, end } : { start: end, end: start };
}

function safePlainTextFromHtml(html: string): string {
  const parsed = new DOMParser().parseFromString(html, 'text/html');
  parsed.querySelectorAll('script,style,template').forEach((node) => node.remove());
  parsed.querySelectorAll<HTMLElement>('*').forEach((node) => {
    const style = node.getAttribute('style') || '';
    if (
      node.hidden ||
      (node.tagName === 'INPUT' && node.getAttribute('type') === 'hidden') ||
      node.getAttribute('aria-hidden') === 'true' ||
      /(?:^|[;\s])(display\s*:\s*none|visibility\s*:\s*hidden)/i.test(style)
    ) {
      node.remove();
    }
  });
  const blockTags = new Set([
    'ADDRESS',
    'ARTICLE',
    'ASIDE',
    'BLOCKQUOTE',
    'DIV',
    'DL',
    'DT',
    'DD',
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
  const visit = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent || '';
    if (node.nodeType !== Node.ELEMENT_NODE) return '';
    const element = node as HTMLElement;
    if (element.tagName === 'BR') return '\n';
    const content = Array.from(element.childNodes).map(visit).join('');
    return blockTags.has(element.tagName) ? `\n${content}\n` : content;
  };
  return visit(parsed.body)
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/^\n+|\n+$/g, '');
}

function setCaretAtOffset(root: HTMLElement, offset: number): void {
  const selection = window.getSelection();
  if (!selection) return;
  const range = document.createRange();
  let remaining = Math.max(0, offset);
  let placed = false;

  const visit = (node: Node) => {
    if (placed) return;
    const element = node.nodeType === Node.ELEMENT_NODE ? (node as HTMLElement) : null;
    if (element?.dataset.composerAttachment) {
      if (remaining <= 0) {
        const parent = node.parentNode || root;
        const siblings: Node[] = Array.from(parent.childNodes);
        range.setStart(parent, siblings.indexOf(node));
        range.collapse(true);
        placed = true;
      } else {
        remaining -= 1;
      }
      return;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      const length = node.textContent?.length || 0;
      if (remaining <= length) {
        range.setStart(node, remaining);
        range.collapse(true);
        placed = true;
      } else {
        remaining -= length;
      }
      return;
    }
    node.childNodes.forEach(visit);
  };

  root.childNodes.forEach(visit);
  if (!placed) {
    range.selectNodeContents(root);
    range.collapse(false);
  }
  selection.removeAllRanges();
  selection.addRange(range);
}

function pointToCaretRange(root: HTMLElement, x: number, y: number): Range | null {
  const documentWithCaret = document as Document & {
    caretRangeFromPoint?: (clientX: number, clientY: number) => Range | null;
    caretPositionFromPoint?: (
      clientX: number,
      clientY: number,
    ) => { offsetNode: Node; offset: number } | null;
  };
  const hasCaretRange = typeof documentWithCaret.caretRangeFromPoint === 'function';
  const hasCaretPosition = typeof documentWithCaret.caretPositionFromPoint === 'function';
  const fromRange = hasCaretRange ? documentWithCaret.caretRangeFromPoint(x, y) : null;
  if (fromRange) return fromRange;
  const position = hasCaretPosition ? documentWithCaret.caretPositionFromPoint(x, y) : null;
  if (position) {
    const range = document.createRange();
    range.setStart(position.offsetNode, position.offset);
    range.collapse(true);
    return range;
  }
  if (hasCaretRange || hasCaretPosition) return null;
  const fallback = document.createRange();
  fallback.selectNodeContents(root);
  fallback.collapse(false);
  return fallback;
}

export const RichTextComposer = forwardRef<RichTextComposerHandle, RichTextComposerProps>(
  function RichTextComposer(
    {
      initialText = '',
      attachments,
      onChange,
      onAttachmentDrop,
      onNativeFiles,
      onNativeInputIssue,
      onRemoveAttachment,
      onKeyDown,
    },
    ref,
  ) {
    const editorRef = useRef<HTMLDivElement>(null);
    const [parts, setParts] = useState<ComposerPart[]>(() =>
      initialText ? [{ type: 'text', value: initialText }] : EMPTY_PARTS,
    );
    const partsRef = useRef(parts);
    const attachmentsRef = useRef(attachments);
    attachmentsRef.current = attachments;
    const attachmentSignature = JSON.stringify(
      attachments.map((attachment) => [
        attachmentOccurrenceId(attachment),
        attachment.attachmentId || '',
        attachment.displayName,
        attachment.href || '',
        attachment.path || '',
      ]),
    );
    const [dropIndicator, setDropIndicator] = useState<DropIndicator | null>(null);
    const dropOffsetRef = useRef<number | null>(null);
    const pendingCaretOffsetRef = useRef<number | null>(null);
    const activeDragPayloadRef = useRef<AttachmentDragPayload | null>(null);

    const publish = useCallback(
      (nextParts: ComposerPart[]) => {
        onChange(valueFromParts(nextParts));
      },
      [onChange],
    );

    const commitReplacement = (start: number, end: number, insertedParts: ComposerPart[]) => {
      const replacement = replaceRange(partsRef.current, start, end, insertedParts);
      for (const occurrenceId of replacement.removedOccurrenceIds) onRemoveAttachment(occurrenceId);
      partsRef.current = replacement.parts;
      setParts(replacement.parts);
      publish(replacement.parts);
      pendingCaretOffsetRef.current =
        start + insertedParts.reduce((total, part) => total + partLength(part), 0);
    };

    useImperativeHandle(
      ref,
      () => ({
        replaceText: (text: string) => {
          const nextParts: ComposerPart[] = text ? [{ type: 'text', value: text }] : EMPTY_PARTS;
          partsRef.current = nextParts;
          setParts(nextParts);
          publish(nextParts);
        },
        replaceValue: (value: ComposerValue) => {
          const nextParts =
            value.parts.length > 0 ? value.parts.map((part) => ({ ...part })) : EMPTY_PARTS;
          partsRef.current = nextParts;
          setParts(nextParts);
          publish(nextParts);
        },
        focus: () => editorRef.current?.focus(),
      }),
      [publish],
    );

    useLayoutEffect(() => {
      if (!editorRef.current) return;
      renderParts(editorRef.current, parts, attachmentsRef.current);
      const offset = pendingCaretOffsetRef.current;
      if (offset === null) return;
      pendingCaretOffsetRef.current = null;
      setCaretAtOffset(editorRef.current, offset);
      editorRef.current.focus();
    }, [parts]);

    useLayoutEffect(() => {
      if (editorRef.current) syncAttachmentNodes(editorRef.current, attachmentsRef.current);
    }, [attachmentSignature]);

    const handleInput = () => {
      if (!editorRef.current) return;
      const previousParts = partsRef.current;
      const nextParts = readParts(editorRef.current);
      const nextAttachmentIds = new Set(
        nextParts
          .filter(
            (part): part is Extract<ComposerPart, { type: 'attachment' }> =>
              part.type === 'attachment',
          )
          .map((part) => partOccurrenceId(part))
          .filter((id): id is string => !!id),
      );
      // Native editing commands such as Ctrl+A + Backspace bypass our atomic
      // key handler and remove content directly from the DOM. An embedded
      // attachment removed by that command is deleted from the pending list as
      // well; only attachments that were never embedded remain standalone
      // chips for Send.
      const removedAttachmentIds = new Set(
        previousParts
          .filter(
            (part): part is Extract<ComposerPart, { type: 'attachment' }> =>
              part.type === 'attachment',
          )
          .map((part) => partOccurrenceId(part))
          .filter((id): id is string => !!id)
          .filter((attachmentId) => !nextAttachmentIds.has(attachmentId)),
      );
      for (const attachmentId of removedAttachmentIds) onRemoveAttachment(attachmentId);
      // The browser-mutated DOM is the source of truth during ordinary typing.
      // Re-rendering it on every input lets React reconcile against a stale
      // contenteditable tree and can duplicate text next to an inline node.
      partsRef.current = nextParts;
      publish(nextParts);
    };

    const removeAt = (attachmentId: string, requestedCaretOffset?: number) => {
      const currentParts = partsRef.current;
      const sourceOffset = attachmentOffset(currentParts, attachmentId);
      const selection = window.getSelection();
      const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
      const currentOffset =
        editorRef.current && range?.collapsed
          ? selectionOffset(editorRef.current, range.startContainer, range.startOffset)
          : null;
      const nextParts = removeAttachment(currentParts, attachmentId);
      if (sourceOffset !== null) {
        const caretOffset =
          requestedCaretOffset ??
          (currentOffset === null
            ? sourceOffset
            : currentOffset > sourceOffset
              ? currentOffset - 1
              : currentOffset);
        pendingCaretOffsetRef.current = Math.max(0, caretOffset);
      }
      partsRef.current = nextParts;
      setParts(nextParts);
      publish(nextParts);
      onRemoveAttachment(attachmentId);
    };

    const handleKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
      const selection = window.getSelection();
      const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
      if (
        range &&
        !range.collapsed &&
        editorRef.current &&
        (event.key === 'Backspace' || event.key === 'Delete')
      ) {
        const offsets = selectedOffsets(editorRef.current);
        if (offsets && offsets.start !== offsets.end) {
          event.preventDefault();
          const replacement = replaceRange(partsRef.current, offsets.start, offsets.end, []);
          for (const occurrenceId of replacement.removedOccurrenceIds)
            onRemoveAttachment(occurrenceId);
          partsRef.current = replacement.parts;
          setParts(replacement.parts);
          publish(replacement.parts);
          pendingCaretOffsetRef.current = offsets.start;
          return;
        }
      }
      if (
        range?.collapsed &&
        editorRef.current &&
        (event.key === 'Backspace' || event.key === 'Delete')
      ) {
        const offset = selectionOffset(editorRef.current, range.startContainer, range.startOffset);
        const targetOffset = event.key === 'Backspace' ? (offset ?? 0) - 1 : (offset ?? 0);
        const target = partsRef.current.reduce<{ id: string | null; cursor: number }>(
          (result, part) => {
            if (result.id) return result;
            const occurrenceId = partOccurrenceId(part);
            if (occurrenceId && result.cursor === targetOffset) result.id = occurrenceId;
            result.cursor += partLength(part);
            return result;
          },
          { id: null, cursor: 0 },
        );
        if (target.id) {
          event.preventDefault();
          const caretOffset =
            event.key === 'Backspace' ? Math.max(0, (offset ?? 0) - 1) : (offset ?? 0);
          removeAt(target.id, caretOffset);
          return;
        }
      }
      onKeyDown?.(event);
    };

    const resolveDragPayload = (dataTransfer: DataTransfer | null): AttachmentDragPayload | null =>
      readAttachmentDragPayload(dataTransfer) || activeDragPayloadRef.current;

    const handleAttachmentDragStart = (event: React.DragEvent<HTMLDivElement>) => {
      const target = event.target instanceof Element ? event.target : null;
      const node = target?.closest<HTMLElement>('[data-composer-attachment]');
      if (!node || !editorRef.current?.contains(node)) return;
      const occurrenceId = node.dataset.composerAttachment;
      const attachment = occurrenceId
        ? attachmentsRef.current.find((item) => attachmentOccurrenceId(item) === occurrenceId)
        : undefined;
      if (!attachment?.href || !isSafeAttachmentHref(attachment.href) || !occurrenceId) {
        event.preventDefault();
        activeDragPayloadRef.current = null;
        return;
      }
      const payload: AttachmentDragPayload = {
        displayName: attachment.displayName,
        href: attachment.href,
        path: attachment.path,
        attachmentId: occurrenceId,
        source: 'composer',
      };
      writeAttachmentDragPayload(event.dataTransfer, payload);
      // Chromium exposes the custom MIME type through `types` during dragover
      // but intentionally returns an empty string from getData(). Keep the
      // source payload here so a node can still be dropped into the editor.
      activeDragPayloadRef.current = payload;
    };

    const handleAttachmentMouseDown = (event: React.MouseEvent<HTMLDivElement>) => {
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest('[data-composer-attachment-delete]')) event.preventDefault();
    };

    const handleAttachmentClick = (event: React.MouseEvent<HTMLDivElement>) => {
      const target = event.target instanceof Element ? event.target : null;
      const button = target?.closest<HTMLButtonElement>('[data-composer-attachment-delete]');
      const attachmentId = button?.dataset.composerAttachmentDelete;
      if (attachmentId) removeAt(attachmentId);
    };

    const clearDropIndicator = () => {
      dropOffsetRef.current = null;
      setDropIndicator(null);
    };

    const updateDropIndicator = (event: React.DragEvent<HTMLDivElement>) => {
      const root = editorRef.current;
      if (!root) return;
      const range = pointToCaretRange(root, event.clientX, event.clientY);
      if (!range) {
        clearDropIndicator();
        return;
      }
      const offset = selectionOffset(root, range.startContainer, range.startOffset);
      if (offset === null) {
        clearDropIndicator();
        return;
      }
      dropOffsetRef.current = offset;
      const rootRect = root.getBoundingClientRect();
      // jsdom and a few embedded WebViews do not implement Range geometry. The
      // x/y fallback still keeps the insertion indicator useful there; browsers
      // with layout support use the precise caret rectangle.
      const rangeRect =
        typeof range.getBoundingClientRect === 'function'
          ? range.getBoundingClientRect()
          : { left: event.clientX, top: event.clientY, height: 0 };
      const left = (rangeRect.left || event.clientX || rootRect.left + 8) - rootRect.left;
      const top = (rangeRect.top || rootRect.top + 8) - rootRect.top;
      setDropIndicator({
        left: Math.max(4, left),
        top: Math.max(4, top),
        height: Math.max(18, rangeRect.height || 20),
      });
    };

    const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
      const payload = resolveDragPayload(event.dataTransfer);
      if (!payload) {
        if (
          hasPanAttachmentMime(event.dataTransfer) ||
          inspectNativeAttachmentInput(event.dataTransfer).kind !== 'none'
        ) {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'copy';
        }
        clearDropIndicator();
        return;
      }
      event.preventDefault();
      event.dataTransfer.dropEffect =
        payload.source === 'composer' || payload.source === 'attachment-chip' ? 'move' : 'copy';
      updateDropIndicator(event);
    };

    const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
      const payload = resolveDragPayload(event.dataTransfer);
      if (!payload) {
        if (hasPanAttachmentMime(event.dataTransfer)) {
          event.preventDefault();
          onNativeInputIssue?.('invalid-pan-attachment');
          clearDropIndicator();
          return;
        }
        const native = inspectNativeAttachmentInput(event.dataTransfer);
        if (native.kind !== 'none') {
          event.preventDefault();
          const root = editorRef.current;
          const range = root ? pointToCaretRange(root, event.clientX, event.clientY) : null;
          const selection = root ? window.getSelection() : null;
          const selectionRange = selection?.rangeCount ? selection.getRangeAt(0) : null;
          const offset =
            dropOffsetRef.current ??
            (root && range
              ? selectionOffset(root, range.startContainer, range.startOffset)
              : null) ??
            (root && selectionRange
              ? selectionOffset(root, selectionRange.startContainer, selectionRange.startOffset)
              : null);
          clearDropIndicator();
          if (native.kind === 'files' && offset !== null) {
            const ids = onNativeFiles?.(native.files, offset, 'drop') || [];
            if (ids.length) {
              commitReplacement(offset, offset, ids.map(attachmentPart));
            }
          } else if (native.kind === 'directory' || native.kind === 'uri') {
            onNativeInputIssue?.(native.kind);
          }
          return;
        }
        clearDropIndicator();
        return;
      }
      event.preventDefault();
      const root = editorRef.current;
      const currentParts = partsRef.current;
      let offset = dropOffsetRef.current;
      if (offset === null && root) {
        const range = pointToCaretRange(root, event.clientX, event.clientY);
        offset = range ? selectionOffset(root, range.startContainer, range.startOffset) : null;
      }
      dropOffsetRef.current = null;
      setDropIndicator(null);
      activeDragPayloadRef.current = null;
      if (offset === null) return;
      const sourceOffset = payload.attachmentId
        ? attachmentOffset(currentParts, payload.attachmentId)
        : null;
      // A stale composer payload must never turn into a second copy of the
      // same node. Chip/message payloads are allowed to create a new inline
      // occurrence; only a missing node from the composer is invalid.
      if (payload.source === 'composer' && payload.attachmentId && sourceOffset === null) return;
      const occurrenceId = onAttachmentDrop(payload);
      if (!occurrenceId) return;
      const withoutSource = payload.attachmentId
        ? removeAttachment(currentParts, payload.attachmentId)
        : currentParts;
      const adjustedOffset = sourceOffset !== null && offset > sourceOffset ? offset - 1 : offset;
      const nextParts = insertAttachment(withoutSource, adjustedOffset, occurrenceId);
      partsRef.current = nextParts;
      setParts(nextParts);
      publish(nextParts);
      pendingCaretOffsetRef.current = adjustedOffset + 1;
    };

    const handlePaste = (event: ReactClipboardEvent<HTMLDivElement>) => {
      const dataTransfer = event.clipboardData;
      const customMime = hasPanAttachmentMime(dataTransfer);
      const payload = customMime ? readAttachmentDragPayload(dataTransfer) : null;
      if (customMime) {
        event.preventDefault();
        if (!payload) {
          onNativeInputIssue?.('invalid-pan-attachment');
          return;
        }
        const root = editorRef.current;
        const offsets = root ? selectedOffsets(root) : null;
        if (!root || !offsets) return;
        // Clipboarding an existing chip is a copy operation.  Clear the local
        // occurrence id so InputRow creates a fresh occurrence for the same
        // server resource instead of duplicating one UI identity.
        const copyPayload = payload.attachmentId
          ? { ...payload, attachmentId: undefined, source: 'message' as const }
          : payload;
        const occurrenceId = onAttachmentDrop(copyPayload);
        if (!occurrenceId) return;
        commitReplacement(offsets.start, offsets.end, [attachmentPart(occurrenceId)]);
        return;
      }
      const native = inspectNativeAttachmentInput(dataTransfer);
      const html = dataTransfer?.getData?.('text/html') || '';
      const richText = html ? safePlainTextFromHtml(html) : '';
      if (html && native.kind !== 'files') {
        event.preventDefault();
        const root = editorRef.current;
        const offsets = root ? selectedOffsets(root) : null;
        if (!root || !offsets) return;
        const fallbackText = dataTransfer?.getData?.('text/plain') || '';
        commitReplacement(offsets.start, offsets.end, [
          { type: 'text', value: richText || fallbackText },
        ]);
        return;
      }
      if (native.kind === 'none') return;
      event.preventDefault();
      if (native.kind === 'files') {
        const root = editorRef.current;
        const offsets = root ? selectedOffsets(root) : null;
        const total = partsRef.current.reduce((sum, part) => sum + partLength(part), 0);
        const start = offsets?.start ?? total;
        const end = offsets?.end ?? start;
        const ids = onNativeFiles?.(native.files, start, 'paste') || [];
        if (ids.length) {
          commitReplacement(start, end, ids.map(attachmentPart));
        }
      } else {
        onNativeInputIssue?.(native.kind);
      }
    };

    const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
        clearDropIndicator();
      }
    };

    const handleDragEnd = () => {
      activeDragPayloadRef.current = null;
      clearDropIndicator();
    };

    useEffect(() => {
      // A drag started in the message list does not bubble its dragend event
      // through this editor. Listen at window level as well so Escape/cancel or
      // a drop outside the editor cannot leave a stale insertion caret behind.
      const rememberGlobalDragPayload = (event: DragEvent) => {
        activeDragPayloadRef.current = readAttachmentDragPayload(event.dataTransfer);
      };
      const clearGlobalDropState = () => {
        activeDragPayloadRef.current = null;
        dropOffsetRef.current = null;
        setDropIndicator(null);
      };
      // This listener runs after the source React handler in the bubble phase,
      // so message links/chips have already populated DataTransfer. Their
      // payload remains available when Chromium protects getData during the
      // subsequent dragover events.
      window.addEventListener('dragstart', rememberGlobalDragPayload);
      window.addEventListener('dragend', clearGlobalDropState);
      window.addEventListener('drop', clearGlobalDropState);
      return () => {
        window.removeEventListener('dragstart', rememberGlobalDragPayload);
        window.removeEventListener('dragend', clearGlobalDropState);
        window.removeEventListener('drop', clearGlobalDropState);
      };
    }, []);

    return (
      <div className="relative min-h-0 flex-1">
        <div
          ref={editorRef}
          contentEditable
          suppressContentEditableWarning
          role="textbox"
          aria-multiline="true"
          aria-label="消息输入框"
          data-testid="rich-text-composer"
          data-placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
          className="composer-editor h-full min-h-0 w-full overflow-y-auto whitespace-pre-wrap break-words rounded border border-border-default bg-bg-tertiary px-3 py-2 text-sm text-text-primary outline-none focus:border-accent"
          onInput={handleInput}
          onPaste={handlePaste}
          onKeyDown={handleKeyDown}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
          onDragLeave={handleDragLeave}
          onDragEnd={handleDragEnd}
          onDragStart={handleAttachmentDragStart}
          onMouseDown={handleAttachmentMouseDown}
          onClick={handleAttachmentClick}
        />
        {dropIndicator && (
          <span
            data-testid="attachment-drop-caret"
            aria-hidden="true"
            className="pointer-events-none absolute z-10 w-0.5 rounded bg-accent shadow-[0_0_0_2px_rgba(9,105,218,0.18)]"
            style={{
              left: dropIndicator.left,
              top: dropIndicator.top,
              height: dropIndicator.height,
            }}
          />
        )}
      </div>
    );
  },
);
