// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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
});
