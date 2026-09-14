// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { RichTextComposer, type ComposerValue } from './RichTextComposer';
import { ATTACHMENT_DRAG_MIME, type AttachmentDragPayload } from '@/utils/attachmentDrag';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, 'caretRangeFromPoint');
  Reflect.deleteProperty(document, 'caretPositionFromPoint');
});

function dragData(payload: AttachmentDragPayload): DataTransfer {
  const data = new Map<string, string>([[ATTACHMENT_DRAG_MIME, JSON.stringify(payload)]]);
  return {
    getData: (type: string) => data.get(type) || '',
    setData: vi.fn(),
    dropEffect: 'copy',
    effectAllowed: 'copy',
  } as unknown as DataTransfer;
}

function renderComposer(onChange: (value: ComposerValue) => void = vi.fn()) {
  const onAttachmentDrop = vi.fn(() => 'attachment-1');
  const onRemoveAttachment = vi.fn();
  render(
    <RichTextComposer
      initialText="before after"
      attachments={[
        {
          id: 'attachment-1',
          displayName: '接口说明.md',
          href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
        },
      ]}
      onChange={onChange}
      onAttachmentDrop={onAttachmentDrop}
      onRemoveAttachment={onRemoveAttachment}
    />,
  );
  return { editor: screen.getByTestId('rich-text-composer'), onAttachmentDrop, onRemoveAttachment };
}

describe('RichTextComposer attachment demo', () => {
  it('shows a live insertion caret while dragging over a text position', () => {
    const { editor } = renderComposer();
    const text = editor.querySelector('span')?.firstChild;
    expect(text).toBeTruthy();
    const range = document.createRange();
    range.setStart(text!, 7);
    range.collapse(true);
    vi.stubGlobal(
      'document',
      Object.assign(document, {
        caretRangeFromPoint: vi.fn(() => range),
      }),
    );

    fireEvent.dragOver(editor, {
      dataTransfer: dragData({
        displayName: '接口说明.md',
        href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
      }),
      clientX: 40,
      clientY: 12,
    });

    expect(screen.getByTestId('attachment-drop-caret')).toBeTruthy();
  });

  it('inserts an icon-bearing attachment node at the caret and preserves text on both sides', () => {
    const changes: ComposerValue[] = [];
    const { editor, onAttachmentDrop } = renderComposer((value) => {
      changes.push(value);
    });
    const text = editor.querySelector('span')?.firstChild;
    const range = document.createRange();
    range.setStart(text!, 7);
    range.collapse(true);
    vi.stubGlobal(
      'document',
      Object.assign(document, {
        caretRangeFromPoint: vi.fn(() => range),
      }),
    );
    const payload = {
      displayName: '接口说明.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
    };

    fireEvent.dragOver(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
    fireEvent.drop(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });

    expect(onAttachmentDrop).toHaveBeenCalledWith(payload);
    const node = screen.getByRole('group', { name: '附件 接口说明.md' });
    expect(node.querySelector('svg')).toBeTruthy();
    expect(node.textContent).toContain('接口说明.md');
    expect(changes.at(-1)).toMatchObject({
      text: 'before after',
      attachmentIds: ['attachment-1'],
      parts: [
        { type: 'text', value: 'before ' },
        { type: 'attachment', attachmentId: 'attachment-1' },
        { type: 'text', value: 'after' },
      ],
    });
  });

  it('keeps ordinary text next to an inserted node exactly once while typing', () => {
    const changes: ComposerValue[] = [];
    const onAttachmentDrop = vi.fn(() => 'attachment-1');
    function RerenderingHarness() {
      const [, rerender] = useState(0);
      return (
        <RichTextComposer
          initialText="before after"
          attachments={[{ id: 'attachment-1', displayName: '接口说明.md' }]}
          onChange={(value) => {
            changes.push(value);
            rerender((count) => count + 1);
          }}
          onAttachmentDrop={onAttachmentDrop}
          onRemoveAttachment={vi.fn()}
        />
      );
    }

    render(<RerenderingHarness />);
    const editor = screen.getByTestId('rich-text-composer');
    const text = editor.querySelector('span')?.firstChild;
    const range = document.createRange();
    range.setStart(text!, 7);
    range.collapse(true);
    vi.stubGlobal(
      'document',
      Object.assign(document, {
        caretRangeFromPoint: vi.fn(() => range),
      }),
    );
    const payload = {
      displayName: '接口说明.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
    };

    fireEvent.dragOver(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
    fireEvent.drop(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
    const afterNode = editor.lastElementChild?.firstChild;
    expect(afterNode).toBeTruthy();
    afterNode!.textContent = 'aftera';
    const singleCharacterCaret = document.createRange();
    singleCharacterCaret.setStart(afterNode!, 'aftera'.length);
    singleCharacterCaret.collapse(true);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(singleCharacterCaret);
    fireEvent.input(editor);

    afterNode!.textContent = 'afterabc';
    const multiCharacterCaret = document.createRange();
    multiCharacterCaret.setStart(afterNode!, 'afterabc'.length);
    multiCharacterCaret.collapse(true);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(multiCharacterCaret);
    fireEvent.input(editor);

    fireEvent.compositionStart(editor);
    afterNode!.textContent = 'afterabc中文';
    const imeCaret = document.createRange();
    imeCaret.setStart(afterNode!, 'afterabc中文'.length);
    imeCaret.collapse(true);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(imeCaret);
    fireEvent.input(editor, {
      data: '中文',
      inputType: 'insertCompositionText',
      isComposing: true,
    });
    fireEvent.compositionEnd(editor, { data: '中文' });

    expect(editor.textContent).toBe('before 接口说明.mdafterabc中文');
    expect(window.getSelection()?.anchorNode).toBe(afterNode);
    expect(window.getSelection()?.anchorOffset).toBe('afterabc中文'.length);
    expect(changes.some((value) => value.text === 'before aftera')).toBe(true);
    expect(changes.some((value) => value.text === 'before afterabc')).toBe(true);
    expect(changes.at(-1)).toMatchObject({
      text: 'before afterabc中文',
      attachmentIds: ['attachment-1'],
    });
  });

  it('serializes browser line breaks from BR and block wrappers without dropping newlines', () => {
    const changes: ComposerValue[] = [];
    const { editor } = renderComposer((value) => {
      changes.push(value);
    });

    editor.innerHTML = '<span>第一行</span><br><span>第二行</span>';
    fireEvent.input(editor);
    expect(changes.at(-1)).toMatchObject({ text: '第一行\n第二行', attachmentIds: [] });

    editor.innerHTML = '<div>第一行</div><div>第二行</div>';
    fireEvent.input(editor);
    expect(changes.at(-1)).toMatchObject({ text: '第一行\n第二行', attachmentIds: [] });

    const secondLineRange = document.createRange();
    secondLineRange.setStart(editor, 1);
    secondLineRange.collapse(true);
    vi.stubGlobal(
      'document',
      Object.assign(document, {
        caretRangeFromPoint: vi.fn(() => secondLineRange),
      }),
    );
    const payload = {
      displayName: '接口说明.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
    };
    fireEvent.dragOver(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
    fireEvent.drop(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
    expect(changes.at(-1)).toMatchObject({
      text: '第一行\n第二行',
      attachmentIds: ['attachment-1'],
      parts: [
        { type: 'text', value: '第一行\n' },
        { type: 'attachment', attachmentId: 'attachment-1' },
        { type: 'text', value: '第二行' },
      ],
    });
  });

  it('treats browser placeholder breaks as empty lines instead of extra newlines', () => {
    const changes: ComposerValue[] = [];
    const { editor } = renderComposer((value) => {
      changes.push(value);
    });

    editor.innerHTML = '<div>第一行</div><div><br></div>';
    fireEvent.input(editor);
    expect(changes.at(-1)?.text).toBe('第一行\n');

    editor.innerHTML = '<div><br></div><div>第二行</div>';
    fireEvent.input(editor);
    expect(changes.at(-1)?.text).toBe('\n第二行');

    editor.innerHTML = '<div>第一行</div><div><br></div><div>第三行</div>';
    fireEvent.input(editor);
    const emptyLineCaret = document.createRange();
    emptyLineCaret.setStart(editor.children[1]!.firstChild!, 0);
    emptyLineCaret.collapse(true);
    vi.stubGlobal(
      'document',
      Object.assign(document, {
        caretRangeFromPoint: vi.fn(() => emptyLineCaret),
      }),
    );
    const payload = {
      displayName: '接口说明.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
    };
    fireEvent.dragOver(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
    fireEvent.drop(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
    expect(changes.at(-1)).toMatchObject({
      text: '第一行\n\n第三行',
      parts: [
        { type: 'text', value: '第一行\n' },
        { type: 'attachment', attachmentId: 'attachment-1' },
        { type: 'text', value: '\n第三行' },
      ],
    });
  });

  it('keeps the caret coordinate after a blank block line', () => {
    const changes: ComposerValue[] = [];
    const attachment = {
      id: 'attachment-1',
      displayName: '接口说明.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
    };
    render(
      <RichTextComposer
        initialText=""
        attachments={[attachment]}
        onChange={(value) => {
          changes.push(value);
        }}
        onAttachmentDrop={vi.fn(() => attachment.id)}
        onRemoveAttachment={vi.fn()}
      />,
    );
    const editor = screen.getByTestId('rich-text-composer');
    editor.innerHTML = '<div>第一行</div><div><br></div><div>第三行</div>';
    fireEvent.input(editor);

    const thirdLineCaret = document.createRange();
    thirdLineCaret.setStart(editor.children[2]!.firstChild!, 0);
    thirdLineCaret.collapse(true);
    vi.stubGlobal(
      'document',
      Object.assign(document, {
        caretRangeFromPoint: vi.fn(() => thirdLineCaret),
      }),
    );
    const payload = { displayName: attachment.displayName, href: attachment.href };
    fireEvent.dragOver(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
    fireEvent.drop(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });

    expect(changes.at(-1)).toMatchObject({
      text: '第一行\n\n第三行',
      parts: [
        { type: 'text', value: '第一行\n\n' },
        { type: 'attachment', attachmentId: attachment.id },
        { type: 'text', value: '第三行' },
      ],
    });
  });

  it('clears the insertion caret for invalid drops and cancelled drags', () => {
    const { editor, onAttachmentDrop } = renderComposer();
    const text = editor.querySelector('span')?.firstChild;
    const range = document.createRange();
    range.setStart(text!, 7);
    range.collapse(true);
    const caretRangeFromPoint = vi.fn(() => range);
    vi.stubGlobal('document', Object.assign(document, { caretRangeFromPoint }));
    const valid = dragData({
      displayName: '接口说明.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
    });
    const invalid = dragData({
      displayName: 'not-an-attachment',
      href: 'https://example.test/not-safe',
    });

    fireEvent.dragOver(editor, { dataTransfer: valid, clientX: 40, clientY: 12 });
    expect(screen.getByTestId('attachment-drop-caret')).toBeTruthy();
    fireEvent.dragOver(editor, { dataTransfer: invalid, clientX: 40, clientY: 12 });
    expect(screen.queryByTestId('attachment-drop-caret')).toBeNull();

    const outsideText = document.createTextNode('outside editor');
    const outsideRange = document.createRange();
    outsideRange.setStart(outsideText, 0);
    outsideRange.collapse(true);
    caretRangeFromPoint.mockReturnValue(outsideRange);
    fireEvent.dragOver(editor, { dataTransfer: valid, clientX: 40, clientY: 12 });
    expect(screen.queryByTestId('attachment-drop-caret')).toBeNull();
    fireEvent.drop(editor, { dataTransfer: valid, clientX: 40, clientY: 12 });
    expect(onAttachmentDrop).not.toHaveBeenCalled();

    caretRangeFromPoint.mockReturnValue(range);
    fireEvent.dragOver(editor, { dataTransfer: valid, clientX: 40, clientY: 12 });
    fireEvent.drop(editor, { dataTransfer: invalid, clientX: 40, clientY: 12 });
    expect(screen.queryByTestId('attachment-drop-caret')).toBeNull();
    expect(onAttachmentDrop).not.toHaveBeenCalled();

    fireEvent.dragOver(editor, { dataTransfer: valid, clientX: 40, clientY: 12 });
    fireEvent.dragEnd(window, { dataTransfer: valid });
    expect(screen.queryByTestId('attachment-drop-caret')).toBeNull();
  });

  it('deletes the whole node and keeps the composer available for continued typing', () => {
    const changes: ComposerValue[] = [];
    const { editor, onRemoveAttachment } = renderComposer((value) => {
      changes.push(value);
    });
    const text = editor.querySelector('span')?.firstChild;
    const range = document.createRange();
    range.setStart(text!, 7);
    range.collapse(true);
    vi.stubGlobal(
      'document',
      Object.assign(document, {
        caretRangeFromPoint: vi.fn(() => range),
      }),
    );
    const payload = {
      displayName: '接口说明.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
    };
    fireEvent.drop(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });

    fireEvent.click(screen.getByRole('button', { name: '删除附件 接口说明.md' }));

    expect(onRemoveAttachment).toHaveBeenCalledWith('attachment-1');
    expect(screen.queryByRole('group', { name: '附件 接口说明.md' })).toBeNull();
    const remainingText = editor.querySelector('span')?.firstChild;
    expect(remainingText).toBeTruthy();
    (remainingText as Text).textContent = 'before after继续输入';
    fireEvent.input(editor);

    expect(changes.at(-1)).toMatchObject({ text: 'before after继续输入', attachmentIds: [] });
  });

  it.each(['Backspace', 'Delete'] as const)(
    'restores focus and the attachment boundary after %s',
    (key) => {
      const { editor, onRemoveAttachment } = renderComposer();
      const text = editor.querySelector('span')?.firstChild;
      const insertionRange = document.createRange();
      insertionRange.setStart(text!, 7);
      insertionRange.collapse(true);
      vi.stubGlobal(
        'document',
        Object.assign(document, {
          caretRangeFromPoint: vi.fn(() => insertionRange),
        }),
      );
      const payload = {
        displayName: '接口说明.md',
        href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
      };
      fireEvent.dragOver(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
      fireEvent.drop(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });

      const caret = document.createRange();
      caret.setStart(editor, key === 'Backspace' ? 2 : 1);
      caret.collapse(true);
      editor.focus();
      window.getSelection()?.removeAllRanges();
      window.getSelection()?.addRange(caret);
      fireEvent.keyDown(editor, { key });

      const remainingText = editor.firstElementChild?.firstChild;
      expect(remainingText).toBeTruthy();
      expect(screen.queryByRole('group', { name: '附件 接口说明.md' })).toBeNull();
      expect(onRemoveAttachment).toHaveBeenCalledWith('attachment-1');
      expect(document.activeElement).toBe(editor);
      expect(window.getSelection()?.anchorNode).toBe(remainingText);
      expect(window.getSelection()?.anchorOffset).toBe(7);
    },
  );

  it('restores the editor focus and caret when deleting through the node button', () => {
    const { editor } = renderComposer();
    const text = editor.querySelector('span')?.firstChild;
    const insertionRange = document.createRange();
    insertionRange.setStart(text!, 7);
    insertionRange.collapse(true);
    vi.stubGlobal(
      'document',
      Object.assign(document, {
        caretRangeFromPoint: vi.fn(() => insertionRange),
      }),
    );
    const payload = {
      displayName: '接口说明.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
    };
    fireEvent.dragOver(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
    fireEvent.drop(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });

    const afterNodeCaret = document.createRange();
    afterNodeCaret.setStart(editor, 2);
    afterNodeCaret.collapse(true);
    editor.focus();
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(afterNodeCaret);
    const removeButton = screen.getByRole('button', { name: '删除附件 接口说明.md' });
    expect(removeButton).toHaveProperty('tabIndex', 0);
    fireEvent.mouseDown(removeButton);
    fireEvent.click(removeButton);

    const remainingText = editor.firstElementChild?.firstChild;
    expect(document.activeElement).toBe(editor);
    expect(window.getSelection()?.anchorNode).toBe(remainingText);
    expect(window.getSelection()?.anchorOffset).toBe(7);
  });

  it('handles external attachments at the start, middle, end, repeated drop, and empty input', () => {
    const changes: ComposerValue[] = [];
    const attachments = ['a', 'b', 'c', 'd'].map((id) => ({
      id,
      displayName: 'same.md',
      href: `/api/attachments/upload_${id.repeat(32)}.md?session_id=s1`,
    }));
    let nextId = 0;
    const onAttachmentDrop = vi.fn(() => attachments[nextId++]?.id || null);
    render(
      <RichTextComposer
        initialText="中"
        attachments={attachments}
        onChange={(value) => {
          changes.push(value);
        }}
        onAttachmentDrop={onAttachmentDrop}
        onRemoveAttachment={vi.fn()}
      />,
    );
    const editor = screen.getByTestId('rich-text-composer');
    const caretRangeFromPoint = vi.fn<() => Range | null>();
    vi.stubGlobal('document', Object.assign(document, { caretRangeFromPoint }));
    const payload = { displayName: 'same.md', href: attachments[0]!.href };
    const dropAt = (range: Range) => {
      caretRangeFromPoint.mockReturnValue(range);
      const data = dragData(payload);
      fireEvent.dragOver(editor, { dataTransfer: data, clientX: 8, clientY: 8 });
      fireEvent.drop(editor, { dataTransfer: data, clientX: 8, clientY: 8 });
    };

    const start = document.createRange();
    start.setStart(editor, 0);
    start.collapse(true);
    dropAt(start);
    const textAfterStart = editor.children[1]?.firstChild;
    const middle = document.createRange();
    middle.setStart(textAfterStart!, 1);
    middle.collapse(true);
    dropAt(middle);
    const end = document.createRange();
    end.setStart(editor, editor.childNodes.length);
    end.collapse(true);
    dropAt(end);
    const repeated = document.createRange();
    repeated.setStart(editor, 0);
    repeated.collapse(true);
    dropAt(repeated);

    expect(
      [...editor.querySelectorAll('[data-composer-attachment]')].map((node) =>
        node.getAttribute('data-composer-attachment'),
      ),
    ).toEqual(['d', 'a', 'b', 'c']);
    expect(editor.textContent).toBe('same.mdsame.md中same.mdsame.md');
    expect(changes.at(-1)).toMatchObject({ text: '中', attachmentIds: ['d', 'a', 'b', 'c'] });

    const emptyChanges: ComposerValue[] = [];
    cleanup();
    Reflect.deleteProperty(document, 'caretRangeFromPoint');
    Reflect.deleteProperty(document, 'caretPositionFromPoint');
    render(
      <RichTextComposer
        initialText=""
        attachments={[attachments[0]!]}
        onChange={(value) => {
          emptyChanges.push(value);
        }}
        onAttachmentDrop={vi.fn(() => 'a')}
        onRemoveAttachment={vi.fn()}
      />,
    );
    const emptyEditor = screen.getByTestId('rich-text-composer');
    const emptyData = dragData(payload);
    fireEvent.drop(emptyEditor, { dataTransfer: emptyData });
    expect(emptyEditor.querySelector('[data-composer-attachment="a"]')).toBeTruthy();
    expect(emptyChanges.at(-1)).toMatchObject({ text: '', attachmentIds: ['a'] });
  });

  it('moves existing nodes without duplicating either node or surrounding text', () => {
    const changes: ComposerValue[] = [];
    const onAttachmentDrop = vi.fn(
      (payload: AttachmentDragPayload) =>
        payload.attachmentId || (payload.displayName === 'b.md' ? 'b' : 'a'),
    );
    const attachments = [
      {
        id: 'a',
        displayName: 'a.md',
        href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
      },
      {
        id: 'b',
        displayName: 'b.md',
        href: '/api/attachments/upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md?session_id=s1',
      },
    ];
    render(
      <RichTextComposer
        initialText="A B"
        attachments={attachments}
        onChange={(value) => {
          changes.push(value);
        }}
        onAttachmentDrop={onAttachmentDrop}
        onRemoveAttachment={vi.fn()}
      />,
    );
    const editor = screen.getByTestId('rich-text-composer');
    const dataFor = (payload: AttachmentDragPayload) => dragData(payload);

    const firstPosition = document.createRange();
    firstPosition.setStart(editor, 0);
    firstPosition.collapse(true);
    vi.stubGlobal(
      'document',
      Object.assign(document, {
        caretRangeFromPoint: vi.fn(() => firstPosition),
      }),
    );
    fireEvent.dragOver(editor, {
      dataTransfer: dataFor({ displayName: 'a.md', href: attachments[0]!.href }),
      clientX: 8,
      clientY: 8,
    });
    fireEvent.drop(editor, {
      dataTransfer: dataFor({ displayName: 'a.md', href: attachments[0]!.href }),
    });

    const textAfterA = editor.children[1]?.firstChild;
    const secondPosition = document.createRange();
    secondPosition.setStart(textAfterA!, 3);
    secondPosition.collapse(true);
    (document.caretRangeFromPoint as ReturnType<typeof vi.fn>).mockReturnValue(secondPosition);
    fireEvent.dragOver(editor, {
      dataTransfer: dataFor({ displayName: 'b.md', href: attachments[1]!.href }),
      clientX: 8,
      clientY: 8,
    });
    fireEvent.drop(editor, {
      dataTransfer: dataFor({ displayName: 'b.md', href: attachments[1]!.href }),
    });

    const nodeA = editor.querySelector('[data-composer-attachment="a"]');
    expect(nodeA).toBeTruthy();
    const moveData = dataFor({
      displayName: 'a.md',
      href: attachments[0]!.href,
      attachmentId: 'a',
      source: 'composer',
    });
    fireEvent.dragStart(nodeA!, { dataTransfer: moveData });
    expect(moveData.effectAllowed).toBe('move');
    expect(moveData.setData).toHaveBeenCalledWith(
      ATTACHMENT_DRAG_MIME,
      expect.stringContaining('"attachmentId":"a"'),
    );
    const movePosition = document.createRange();
    movePosition.setStart(editor, editor.childNodes.length);
    movePosition.collapse(true);
    (document.caretRangeFromPoint as ReturnType<typeof vi.fn>).mockReturnValue(movePosition);
    fireEvent.dragOver(editor, { dataTransfer: moveData, clientX: 8, clientY: 8 });
    expect(screen.getByTestId('attachment-drop-caret')).toBeTruthy();
    fireEvent.drop(editor, { dataTransfer: moveData, clientX: 8, clientY: 8 });

    expect(
      [...editor.querySelectorAll('[data-composer-attachment]')].map((node) =>
        node.getAttribute('data-composer-attachment'),
      ),
    ).toEqual(['b', 'a']);
    expect(editor.textContent).toBe('A Bb.mda.md');
    expect(changes.at(-1)).toMatchObject({ text: 'A B', attachmentIds: ['b', 'a'] });

    const nodeB = editor.querySelector('[data-composer-attachment="b"]');
    expect(nodeB).toBeTruthy();
    const selfDropData = dataFor({
      displayName: 'b.md',
      href: attachments[1]!.href,
      attachmentId: 'b',
      source: 'composer',
    });
    fireEvent.dragStart(nodeB!, { dataTransfer: selfDropData });
    const samePosition = document.createRange();
    samePosition.setStart(editor, 1);
    samePosition.collapse(true);
    (document.caretRangeFromPoint as ReturnType<typeof vi.fn>).mockReturnValue(samePosition);
    fireEvent.dragOver(editor, { dataTransfer: selfDropData, clientX: 8, clientY: 8 });
    fireEvent.drop(editor, { dataTransfer: selfDropData, clientX: 8, clientY: 8 });

    expect(
      [...editor.querySelectorAll('[data-composer-attachment]')].map((node) =>
        node.getAttribute('data-composer-attachment'),
      ),
    ).toEqual(['b', 'a']);
    expect(editor.textContent).toBe('A Bb.mda.md');
    expect(changes.at(-1)).toMatchObject({ text: 'A B', attachmentIds: ['b', 'a'] });
    expect(onAttachmentDrop).toHaveBeenCalledTimes(4);

    // A second move after a no-op self-drop must use the current flat model,
    // not a stale DOM/React snapshot, and must not duplicate either node.
    const moveBackData = dataFor({
      displayName: 'a.md',
      href: attachments[0]!.href,
      attachmentId: 'a',
      source: 'composer',
    });
    const nodeAAtEnd = editor.querySelector('[data-composer-attachment="a"]');
    expect(nodeAAtEnd).toBeTruthy();
    fireEvent.dragStart(nodeAAtEnd!, { dataTransfer: moveBackData });
    const moveBackPosition = document.createRange();
    moveBackPosition.setStart(editor, 0);
    moveBackPosition.collapse(true);
    (document.caretRangeFromPoint as ReturnType<typeof vi.fn>).mockReturnValue(moveBackPosition);
    fireEvent.dragOver(editor, { dataTransfer: moveBackData, clientX: 8, clientY: 8 });
    expect(screen.getByTestId('attachment-drop-caret')).toBeTruthy();
    fireEvent.drop(editor, { dataTransfer: moveBackData, clientX: 8, clientY: 8 });

    expect(
      [...editor.querySelectorAll('[data-composer-attachment]')].map((node) =>
        node.getAttribute('data-composer-attachment'),
      ),
    ).toEqual(['a', 'b']);
    expect(editor.textContent).toBe('a.mdA Bb.md');
    expect(changes.at(-1)).toMatchObject({ text: 'A B', attachmentIds: ['a', 'b'] });
    expect(onAttachmentDrop).toHaveBeenCalledTimes(5);
  });

  it('pastes multiple OS files as inline nodes and does not fall through to text', () => {
    const changes: ComposerValue[] = [];
    const files = [
      new File(['a'], 'a.txt', { type: 'text/plain' }),
      new File(['b'], 'b.txt', { type: 'text/plain' }),
    ];
    const editor = render(
      <RichTextComposer
        initialText="left right"
        attachments={files.map((file, index) => ({ id: `file-${index}`, displayName: file.name }))}
        onChange={(value) => changes.push(value)}
        onAttachmentDrop={vi.fn(() => null)}
        onNativeFiles={vi.fn(() => ['file-0', 'file-1'])}
        onRemoveAttachment={vi.fn()}
      />,
    ).getByTestId('rich-text-composer');
    const text = editor.firstElementChild?.firstChild;
    const range = document.createRange();
    range.setStart(text!, 5);
    range.collapse(true);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
    const clipboardData = {
      files,
      items: [],
      types: ['Files'],
      getData: vi.fn(() => ''),
    } as unknown as DataTransfer;
    fireEvent.paste(editor, { clipboardData });
    expect(editor.textContent).toContain('left a.txtb.txt');
    expect(editor.querySelectorAll('[data-composer-attachment]')).toHaveLength(2);
    expect(changes.at(-1)?.attachmentIds).toEqual(['file-0', 'file-1']);
  });

  it('gives Pan custom MIME precedence over a simultaneous OS file', () => {
    const onAttachmentDrop = vi.fn(() => 'pan-attachment');
    const onNativeFiles = vi.fn(() => ['os-file']);
    const editor = render(
      <RichTextComposer
        initialText="text"
        attachments={[
          { id: 'pan-attachment', displayName: 'Pan.md' },
          { id: 'os-file', displayName: 'OS.txt' },
        ]}
        onChange={vi.fn()}
        onAttachmentDrop={onAttachmentDrop}
        onNativeFiles={onNativeFiles}
        onRemoveAttachment={vi.fn()}
      />,
    ).getByTestId('rich-text-composer');
    const dataTransfer = {
      types: [ATTACHMENT_DRAG_MIME, 'Files'],
      files: [new File(['os'], 'OS.txt')],
      items: [],
      getData: (type: string) =>
        type === ATTACHMENT_DRAG_MIME
          ? JSON.stringify({
              displayName: 'Pan.md',
              href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
            })
          : '',
    } as unknown as DataTransfer;
    fireEvent.drop(editor, { dataTransfer });
    expect(onAttachmentDrop).toHaveBeenCalledTimes(1);
    expect(onNativeFiles).not.toHaveBeenCalled();
  });

  it('replaces the selected range with native files and keeps File objects untouched', () => {
    const file = new File(['original bytes'], 'bytes.html', { type: 'text/html' });
    const onNativeFiles = vi.fn(() => ['file-occurrence']);
    const onRemoveAttachment = vi.fn();
    render(
      <RichTextComposer
        initialText="abcdef"
        attachments={[{ id: 'file-occurrence', displayName: file.name }]}
        onChange={vi.fn()}
        onAttachmentDrop={vi.fn(() => null)}
        onNativeFiles={onNativeFiles}
        onRemoveAttachment={onRemoveAttachment}
      />,
    );
    const editor = screen.getByTestId('rich-text-composer');
    const text = editor.firstElementChild?.firstChild;
    const selection = document.createRange();
    selection.setStart(text!, 1);
    selection.setEnd(text!, 4);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(selection);
    const clipboardData = {
      files: [file],
      items: [],
      types: ['Files', 'text/html'],
      getData: vi.fn(() => '<b>must not be inserted as html</b>'),
    } as unknown as DataTransfer;

    fireEvent.paste(editor, { clipboardData });
    expect(onNativeFiles).toHaveBeenCalledWith([file], 1, 'paste');
    expect(editor.textContent).toBe('abytes.htmlef');
    expect(editor.querySelector('[data-composer-attachment="file-occurrence"]')).toBeTruthy();
    expect(onRemoveAttachment).not.toHaveBeenCalled();
  });

  it('sanitizes rich HTML to text, preserves line breaks, and replaces the selection', () => {
    const changes: ComposerValue[] = [];
    render(
      <RichTextComposer
        initialText="abcdef"
        attachments={[]}
        onChange={(value) => changes.push(value)}
        onAttachmentDrop={vi.fn(() => null)}
        onRemoveAttachment={vi.fn()}
      />,
    );
    const editor = screen.getByTestId('rich-text-composer');
    const text = editor.firstElementChild?.firstChild;
    const selection = document.createRange();
    selection.setStart(text!, 2);
    selection.setEnd(text!, 4);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(selection);
    const clipboardData = {
      files: [],
      items: [],
      types: ['text/html', 'text/plain'],
      getData: (type: string) =>
        type === 'text/html'
          ? '<div>安全一<br>安全二</div><script>恶意()</script><style>.x{}</style><span hidden>隐藏</span>'
          : 'fallback',
    } as unknown as DataTransfer;

    fireEvent.paste(editor, { clipboardData });
    expect(editor.textContent).toBe('ab安全一\n安全二ef');
    expect(editor.innerHTML).not.toContain('script');
    expect(editor.innerHTML).not.toContain('恶意');
    expect(editor.innerHTML).not.toContain('隐藏');
    expect(changes.at(-1)?.text).toBe('ab安全一\n安全二ef');
  });

  it('deletes a selected attachment atomically on Ctrl+A/Delete', () => {
    const changes: ComposerValue[] = [];
    const { editor } = renderComposer((value) => changes.push(value));
    const text = editor.querySelector('span')?.firstChild;
    const insert = document.createRange();
    insert.setStart(text!, 7);
    insert.collapse(true);
    vi.stubGlobal(
      'document',
      Object.assign(document, { caretRangeFromPoint: vi.fn(() => insert) }),
    );
    const payload = {
      displayName: '接口说明.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
    };
    fireEvent.drop(editor, { dataTransfer: dragData(payload) });

    const all = document.createRange();
    all.selectNodeContents(editor);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(all);
    fireEvent.keyDown(editor, { key: 'Delete', ctrlKey: true });
    fireEvent.input(editor);

    expect(editor.querySelector('[data-composer-attachment]')).toBeNull();
    expect(changes.at(-1)?.attachmentIds).toEqual([]);
    // The InputRow callback is responsible for removing the pending chip;
    // the standalone composer still exposes the atomic node id.
    expect(editor.textContent).toBe('');
  });
});
