// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { inspectNativeAttachmentInput } from './nativeAttachmentInput';

function transfer(partial: Partial<DataTransfer>): DataTransfer {
  return partial as DataTransfer;
}

describe('native attachment input inspection', () => {
  it('returns all ordinary files and ignores fake paths', () => {
    const first = new File(['a'], 'a.txt', { type: 'text/plain' });
    const second = new File(['bb'], 'b.txt', { type: 'text/plain' });
    const result = inspectNativeAttachmentInput(transfer({ files: [first, second] as unknown as FileList, items: [] as unknown as DataTransferItemList }));
    expect(result).toEqual({ kind: 'files', files: [first, second] });
    expect((result.kind === 'files' ? result.files[0] : null)?.name).toBe('a.txt');
  });

  it('rejects directory entries and file URIs without turning them into text', () => {
    const directoryItem = {
      kind: 'file',
      getAsFile: () => null,
      webkitGetAsEntry: () => ({ isDirectory: true }),
    } as unknown as DataTransferItem;
    expect(inspectNativeAttachmentInput(transfer({ items: [directoryItem] as unknown as DataTransferItemList, files: [] as unknown as FileList }))).toEqual({ kind: 'directory' });
    expect(inspectNativeAttachmentInput(transfer({
      items: [] as unknown as DataTransferItemList,
      files: [] as unknown as FileList,
      getData: (type: string) => type === 'text/uri-list' ? 'file:///C:/tmp/folder' : '',
    }))).toEqual({ kind: 'uri' });
  });
});
