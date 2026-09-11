import { describe, expect, it, vi } from 'vitest';
import {
  directoryEntryExists,
  parseDirectoryInput,
  parentDirectory,
} from './directoryInput';

describe('parseDirectoryInput', () => {
  it.each([
    ['D:\\project\\app\\re', { directory: 'D:\\project\\app', search: 're' }],
    ['D:\\', { directory: 'D:\\', search: '' }],
    ['D:', { directory: 'D:\\', search: '' }],
    ['D:\\project\\', { directory: 'D:\\project', search: '' }],
    ['D:\\project\\\\', { directory: 'D:\\project\\', search: '' }],
    ['\\\\server\\share\\file', { directory: '\\\\server\\share', search: 'file' }],
    ['', { directory: '', search: '' }],
    ['relative-worktree', { directory: '', search: 'relative-worktree' }],
  ])('splits %j at the final separator', (input, expected) => {
    expect(parseDirectoryInput(input)).toMatchObject(expected);
  });

  it('does not sanitize illegal characters into a different path', () => {
    const parsed = parseDirectoryInput('D:\\bad<name\\file*');
    expect(parsed.directory).toBe('D:\\bad<name');
    expect(parsed.search).toBe('file*');
    expect(parsed.candidate).toBe('D:\\bad<name\\file*');
  });
});

describe('directoryEntryExists', () => {
  it('re-lists the parent and matches the final path case-insensitively', async () => {
    const fetcher = vi.fn(async () => ({
      current: 'D:\\attachments',
      parent: 'D:\\',
      entries: [{ name: 'Report.TXT', path: 'D:\\attachments\\Report.TXT', isDirectory: false }],
    }));
    await expect(directoryEntryExists('D:\\attachments\\report.txt', fetcher)).resolves.toBe(true);
    expect(fetcher).toHaveBeenCalledWith('D:\\attachments', true);
  });

  it('fails closed for a path without a parent separator', async () => {
    const fetcher = vi.fn();
    await expect(directoryEntryExists('report.txt', fetcher)).resolves.toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

it('keeps the parent parser shared by final validation', () => {
  expect(parentDirectory('D:\\attachments\\report.txt')).toBe('D:\\attachments');
  expect(parentDirectory('D:\\')).toBe('D:\\');
});
