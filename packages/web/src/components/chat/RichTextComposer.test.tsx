// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { RichTextComposer, type ComposerValue } from './RichTextComposer';
import { ATTACHMENT_DRAG_MIME, type AttachmentDragPayload } from '@/utils/attachmentDrag';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
      attachments={[{ id: 'attachment-1', displayName: '接口说明.md' }]}
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
    vi.stubGlobal('document', Object.assign(document, {
      caretRangeFromPoint: vi.fn(() => range),
    }));

    fireEvent.dragOver(editor, { dataTransfer: dragData({
      displayName: '接口说明.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
    }), clientX: 40, clientY: 12 });

    expect(screen.getByTestId('attachment-drop-caret')).toBeTruthy();
  });

  it('inserts an icon-bearing attachment node at the caret and preserves text on both sides', () => {
    const changes: ComposerValue[] = [];
    const { editor, onAttachmentDrop } = renderComposer((value) => { changes.push(value); });
    const text = editor.querySelector('span')?.firstChild;
    const range = document.createRange();
    range.setStart(text!, 7);
    range.collapse(true);
    vi.stubGlobal('document', Object.assign(document, {
      caretRangeFromPoint: vi.fn(() => range),
    }));
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
    vi.stubGlobal('document', Object.assign(document, {
      caretRangeFromPoint: vi.fn(() => range),
    }));
    const payload = {
      displayName: '接口说明.md',
      href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1',
    };

    fireEvent.dragOver(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
    fireEvent.drop(editor, { dataTransfer: dragData(payload), clientX: 40, clientY: 12 });
    const afterNode = editor.lastElementChild?.firstChild;
    expect(afterNode).toBeTruthy();
    afterNode!.textContent = 'after继续输入';
    fireEvent.input(editor);

    expect(editor.textContent).toBe('before 接口说明.mdafter继续输入');
    expect(changes.at(-1)).toMatchObject({ text: 'before after继续输入', attachmentIds: ['attachment-1'] });
  });

  it('deletes the whole node and keeps the composer available for continued typing', () => {
    const changes: ComposerValue[] = [];
    const { editor, onRemoveAttachment } = renderComposer((value) => { changes.push(value); });
    const text = editor.querySelector('span')?.firstChild;
    const range = document.createRange();
    range.setStart(text!, 7);
    range.collapse(true);
    vi.stubGlobal('document', Object.assign(document, {
      caretRangeFromPoint: vi.fn(() => range),
    }));
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

  it('moves existing nodes without duplicating either node or surrounding text', () => {
    const changes: ComposerValue[] = [];
    const onAttachmentDrop = vi.fn((payload: AttachmentDragPayload) => (
      payload.attachmentId || (payload.displayName === 'b.md' ? 'b' : 'a')
    ));
    const attachments = [
      { id: 'a', displayName: 'a.md', href: '/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.md?session_id=s1' },
      { id: 'b', displayName: 'b.md', href: '/api/attachments/upload_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.md?session_id=s1' },
    ];
    render(
      <RichTextComposer
        initialText="A B"
        attachments={attachments}
        onChange={(value) => { changes.push(value); }}
        onAttachmentDrop={onAttachmentDrop}
        onRemoveAttachment={vi.fn()}
      />,
    );
    const editor = screen.getByTestId('rich-text-composer');
    const dataFor = (payload: AttachmentDragPayload) => dragData(payload);

    const firstPosition = document.createRange();
    firstPosition.setStart(editor, 0);
    firstPosition.collapse(true);
    vi.stubGlobal('document', Object.assign(document, {
      caretRangeFromPoint: vi.fn(() => firstPosition),
    }));
    fireEvent.dragOver(editor, { dataTransfer: dataFor({ displayName: 'a.md', href: attachments[0]!.href }), clientX: 8, clientY: 8 });
    fireEvent.drop(editor, { dataTransfer: dataFor({ displayName: 'a.md', href: attachments[0]!.href }) });

    const textAfterA = editor.children[1]?.firstChild;
    const secondPosition = document.createRange();
    secondPosition.setStart(textAfterA!, 3);
    secondPosition.collapse(true);
    (document.caretRangeFromPoint as ReturnType<typeof vi.fn>).mockReturnValue(secondPosition);
    fireEvent.dragOver(editor, { dataTransfer: dataFor({ displayName: 'b.md', href: attachments[1]!.href }), clientX: 8, clientY: 8 });
    fireEvent.drop(editor, { dataTransfer: dataFor({ displayName: 'b.md', href: attachments[1]!.href }) });

    const nodeA = editor.querySelector('[data-composer-attachment="a"]');
    expect(nodeA).toBeTruthy();
    const moveData = dataFor({
      displayName: 'a.md', href: attachments[0]!.href, attachmentId: 'a', source: 'composer',
    });
    fireEvent.dragStart(nodeA!, { dataTransfer: moveData });
    const movePosition = document.createRange();
    movePosition.setStart(editor, editor.childNodes.length);
    movePosition.collapse(true);
    (document.caretRangeFromPoint as ReturnType<typeof vi.fn>).mockReturnValue(movePosition);
    fireEvent.dragOver(editor, { dataTransfer: moveData, clientX: 8, clientY: 8 });
    expect(screen.getByTestId('attachment-drop-caret')).toBeTruthy();
    fireEvent.drop(editor, { dataTransfer: moveData, clientX: 8, clientY: 8 });

    expect([...editor.querySelectorAll('[data-composer-attachment]')].map((node) => node.getAttribute('data-composer-attachment')))
      .toEqual(['b', 'a']);
    expect(editor.textContent).toBe('A Bb.mda.md');
    expect(changes.at(-1)).toMatchObject({ text: 'A B', attachmentIds: ['b', 'a'] });

    const nodeB = editor.querySelector('[data-composer-attachment="b"]');
    expect(nodeB).toBeTruthy();
    const selfDropData = dataFor({
      displayName: 'b.md', href: attachments[1]!.href, attachmentId: 'b', source: 'composer',
    });
    fireEvent.dragStart(nodeB!, { dataTransfer: selfDropData });
    const samePosition = document.createRange();
    samePosition.setStart(editor, 1);
    samePosition.collapse(true);
    (document.caretRangeFromPoint as ReturnType<typeof vi.fn>).mockReturnValue(samePosition);
    fireEvent.dragOver(editor, { dataTransfer: selfDropData, clientX: 8, clientY: 8 });
    fireEvent.drop(editor, { dataTransfer: selfDropData, clientX: 8, clientY: 8 });

    expect([...editor.querySelectorAll('[data-composer-attachment]')].map((node) => node.getAttribute('data-composer-attachment')))
      .toEqual(['b', 'a']);
    expect(editor.textContent).toBe('A Bb.mda.md');
    expect(changes.at(-1)).toMatchObject({ text: 'A B', attachmentIds: ['b', 'a'] });
    expect(onAttachmentDrop).toHaveBeenCalledTimes(4);
  });
});
