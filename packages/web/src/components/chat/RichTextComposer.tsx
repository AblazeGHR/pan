import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import { File as FileIcon, X } from 'lucide-react';
import {
  readAttachmentDragPayload,
  writeAttachmentDragPayload,
  type AttachmentDragPayload,
} from '@/utils/attachmentDrag';

export type ComposerPart =
  | { type: 'text'; value: string }
  | { type: 'attachment'; attachmentId: string };

export interface ComposerValue {
  parts: ComposerPart[];
  text: string;
  attachmentIds: string[];
}

export interface RichTextComposerHandle {
  replaceText: (text: string) => void;
  focus: () => void;
}

interface RichTextComposerProps {
  initialText?: string;
  attachments: Array<{ id: string; displayName: string; href?: string; path?: string }>;
  onChange: (value: ComposerValue) => void;
  onAttachmentDrop: (payload: AttachmentDragPayload) => string | null;
  onRemoveAttachment: (attachmentId: string) => void;
  onKeyDown?: (event: ReactKeyboardEvent<HTMLElement>) => void;
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

function partLength(part: ComposerPart): number {
  return part.type === 'text' ? part.value.length : 1;
}

function attachmentOffset(parts: ComposerPart[], attachmentId: string): number | null {
  let offset = 0;
  for (const part of parts) {
    if (part.type === 'attachment' && part.attachmentId === attachmentId) return offset;
    offset += partLength(part);
  }
  return null;
}

function valueFromParts(parts: ComposerPart[]): ComposerValue {
  const normalized = mergeTextParts(parts);
  return {
    parts: normalized,
    text: normalized
      .filter((part): part is Extract<ComposerPart, { type: 'text' }> => part.type === 'text')
      .map((part) => part.value)
      .join(''),
    attachmentIds: normalized
      .filter((part): part is Extract<ComposerPart, { type: 'attachment' }> => part.type === 'attachment')
      .map((part) => part.attachmentId),
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
      const element = node.nodeType === Node.ELEMENT_NODE ? node as HTMLElement : null;
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
        parts.push({ type: 'attachment', attachmentId: element.dataset.composerAttachment });
        result = { hasContent: true, endsWithNewline: false };
      } else if (element?.tagName === 'BR') {
        const isPlaceholder = !!container
          && BLOCK_TAGS.has(container.tagName)
          && container.childNodes.length === 1
          && container.firstChild === element;
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
  return mergeTextParts(parts.filter((part) => (
    part.type !== 'attachment' || part.attachmentId !== attachmentId
  )));
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
        result.push({ type: 'attachment', attachmentId });
        result.push({ type: 'text', value: part.value.slice(remaining) });
      } else if (remaining === 0) {
        result.push({ type: 'attachment', attachmentId }, part);
      } else {
        result.push(part, { type: 'attachment', attachmentId });
      }
      inserted = true;
      continue;
    }
    result.push(part);
    remaining -= length;
  }

  if (!inserted) result.push({ type: 'attachment', attachmentId });
  return mergeTextParts(result);
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
      return !(container && BLOCK_TAGS.has(container.tagName)
        && container.childNodes.length === 1 && container.firstChild === element);
    }
    return Array.from(element.childNodes).some((child) => hasLogicalContent(child, element));
  };

  const addBlockSeparator = (element: HTMLElement) => {
    if (BLOCK_TAGS.has(element.tagName)
        && (cursor.lastBlockEmpty || cursor.hasContent && !cursor.endsWithNewline)) {
      cursor.total += 1;
      cursor.endsWithNewline = true;
    }
  };

  const consume = (node: Node, container: HTMLElement | null): boolean => {
    const element = node.nodeType === Node.ELEMENT_NODE ? node as HTMLElement : null;
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
      if (container && BLOCK_TAGS.has(container.tagName)
          && container.childNodes.length === 1 && container.firstChild === element) {
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
    cursor.lastBlockEmpty = !!element && BLOCK_TAGS.has(element.tagName)
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
        const element = node.nodeType === Node.ELEMENT_NODE ? node as HTMLElement : null;
        if (element?.dataset.composerAttachment) {
          found = cursor.total + Math.min(offset, 1);
        } else if (element?.tagName === 'BR' && container && BLOCK_TAGS.has(container.tagName)
                   && container.childNodes.length === 1 && container.firstChild === element) {
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

function setCaretAtOffset(root: HTMLElement, offset: number): void {
  const selection = window.getSelection();
  if (!selection) return;
  const range = document.createRange();
  let remaining = Math.max(0, offset);
  let placed = false;

  const visit = (node: Node) => {
    if (placed) return;
    const element = node.nodeType === Node.ELEMENT_NODE ? node as HTMLElement : null;
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
    caretPositionFromPoint?: (clientX: number, clientY: number) => { offsetNode: Node; offset: number } | null;
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

export const RichTextComposer = forwardRef<RichTextComposerHandle, RichTextComposerProps>(function RichTextComposer({
  initialText = '',
  attachments,
  onChange,
  onAttachmentDrop,
  onRemoveAttachment,
  onKeyDown,
}, ref) {
  const editorRef = useRef<HTMLDivElement>(null);
  const [parts, setParts] = useState<ComposerPart[]>(() => initialText ? [{ type: 'text', value: initialText }] : EMPTY_PARTS);
  const partsRef = useRef(parts);
  const [dropIndicator, setDropIndicator] = useState<DropIndicator | null>(null);
  const dropOffsetRef = useRef<number | null>(null);
  const pendingCaretOffsetRef = useRef<number | null>(null);

  const publish = useCallback((nextParts: ComposerPart[]) => {
    onChange(valueFromParts(nextParts));
  }, [onChange]);

  useImperativeHandle(ref, () => ({
    replaceText: (text: string) => {
      const nextParts: ComposerPart[] = text ? [{ type: 'text', value: text }] : EMPTY_PARTS;
      partsRef.current = nextParts;
      setParts(nextParts);
      publish(nextParts);
    },
    focus: () => editorRef.current?.focus(),
  }), [publish]);

  useLayoutEffect(() => {
    const offset = pendingCaretOffsetRef.current;
    if (offset === null || !editorRef.current) return;
    pendingCaretOffsetRef.current = null;
    setCaretAtOffset(editorRef.current, offset);
    editorRef.current.focus();
  }, [parts]);

  const handleInput = () => {
    if (!editorRef.current) return;
    const nextParts = readParts(editorRef.current);
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
    const currentOffset = editorRef.current && range?.collapsed
      ? selectionOffset(editorRef.current, range.startContainer, range.startOffset)
      : null;
    const nextParts = removeAttachment(currentParts, attachmentId);
    if (sourceOffset !== null) {
      const caretOffset = requestedCaretOffset ?? (currentOffset === null
        ? sourceOffset
        : currentOffset > sourceOffset ? currentOffset - 1 : currentOffset);
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
    if (range?.collapsed && editorRef.current && (event.key === 'Backspace' || event.key === 'Delete')) {
      const offset = selectionOffset(editorRef.current, range.startContainer, range.startOffset);
      const targetOffset = event.key === 'Backspace' ? (offset ?? 0) - 1 : (offset ?? 0);
      const target = partsRef.current.reduce<{ id: string | null; cursor: number }>((result, part) => {
        if (result.id) return result;
        if (part.type === 'attachment' && result.cursor === targetOffset) result.id = part.attachmentId;
        result.cursor += partLength(part);
        return result;
      }, { id: null, cursor: 0 });
      if (target.id) {
        event.preventDefault();
        const caretOffset = event.key === 'Backspace'
          ? Math.max(0, (offset ?? 0) - 1)
          : offset ?? 0;
        removeAt(target.id, caretOffset);
        return;
      }
    }
    onKeyDown?.(event);
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
    const rangeRect = typeof range.getBoundingClientRect === 'function'
      ? range.getBoundingClientRect()
      : { left: event.clientX, top: event.clientY, height: 0 };
    const left = (rangeRect.left || (event.clientX || rootRect.left + 8)) - rootRect.left;
    const top = (rangeRect.top || rootRect.top + 8) - rootRect.top;
    setDropIndicator({ left: Math.max(4, left), top: Math.max(4, top), height: Math.max(18, rangeRect.height || 20) });
  };

  const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    const payload = readAttachmentDragPayload(event.dataTransfer);
    if (!payload) {
      clearDropIndicator();
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = payload.source === 'composer' || payload.source === 'attachment-chip'
      ? 'move'
      : 'copy';
    updateDropIndicator(event);
  };

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    const payload = readAttachmentDragPayload(event.dataTransfer);
    if (!payload) {
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
    if (offset === null) return;
    const attachmentId = onAttachmentDrop(payload);
    if (!attachmentId) return;
    const sourceOffset = payload.attachmentId
      ? attachmentOffset(currentParts, payload.attachmentId)
      : null;
    const withoutSource = payload.attachmentId
      ? removeAttachment(currentParts, payload.attachmentId)
      : currentParts;
    const adjustedOffset = sourceOffset !== null && offset > sourceOffset ? offset - 1 : offset;
    const nextParts = insertAttachment(withoutSource, adjustedOffset, attachmentId);
    partsRef.current = nextParts;
    setParts(nextParts);
    publish(nextParts);
    pendingCaretOffsetRef.current = adjustedOffset + 1;
  };

  const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      clearDropIndicator();
    }
  };

  const handleDragEnd = () => {
    clearDropIndicator();
  };

  useEffect(() => {
    // A drag started in the message list does not bubble its dragend event
    // through this editor. Listen at window level as well so Escape/cancel or
    // a drop outside the editor cannot leave a stale insertion caret behind.
    const clearGlobalDropState = () => {
      dropOffsetRef.current = null;
      setDropIndicator(null);
    };
    window.addEventListener('dragend', clearGlobalDropState);
    window.addEventListener('drop', clearGlobalDropState);
    return () => {
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
        onKeyDown={handleKeyDown}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        onDragLeave={handleDragLeave}
        onDragEnd={handleDragEnd}
      >
        {parts.map((part, index) => part.type === 'text' ? (
          <span key={`text-${index}`}>{part.value}</span>
        ) : (() => {
          const attachment = attachments.find((item) => item.id === part.attachmentId);
          if (!attachment) return null;
          return (
            <span
              key={part.attachmentId}
              data-composer-attachment={part.attachmentId}
              contentEditable={false}
              draggable={!!attachment.href}
              role="group"
              aria-label={`附件 ${attachment.displayName}`}
              className="composer-attachment-node mx-0.5 inline-flex max-w-full select-none items-center gap-1 rounded border border-accent/50 bg-accent/10 px-1.5 py-0.5 align-baseline text-xs text-accent"
              onDragStart={(event) => {
                if (!attachment.href) return;
                event.stopPropagation();
                writeAttachmentDragPayload(event.dataTransfer, {
                  displayName: attachment.displayName,
                  href: attachment.href,
                  path: attachment.path,
                  attachmentId: attachment.id,
                  source: 'composer',
                });
              }}
            >
              <FileIcon size={13} className="shrink-0" aria-hidden="true" />
              <span className="max-w-[14rem] truncate">{attachment.displayName}</span>
              <button
                type="button"
                aria-label={`删除附件 ${attachment.displayName}`}
                className="ml-0.5 shrink-0 rounded p-0.5 text-accent/80 hover:bg-accent/20 hover:text-accent"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => removeAt(attachment.id)}
              >
                <X size={12} aria-hidden="true" />
              </button>
            </span>
          );
        })())}
      </div>
      {dropIndicator && (
        <span
          data-testid="attachment-drop-caret"
          aria-hidden="true"
          className="pointer-events-none absolute z-10 w-0.5 rounded bg-accent shadow-[0_0_0_2px_rgba(9,105,218,0.18)]"
          style={{ left: dropIndicator.left, top: dropIndicator.top, height: dropIndicator.height }}
        />
      )}
    </div>
  );
});
