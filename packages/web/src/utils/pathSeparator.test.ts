import { describe, expect, it } from 'vitest';
import { appendPathSeparator, isWindowsPath, pathSeparator } from './pathSeparator';

describe('pathSeparator', () => {
  it('detects Windows drive roots and keeps the spelling the user typed', () => {
    expect(pathSeparator('D:\\project')).toBe('\\');
    expect(pathSeparator('D:/project')).toBe('/');
    expect(pathSeparator('C:\\')).toBe('\\');
    // Bare drive: drive-relative in Windows APIs, treated as the drive root.
    expect(pathSeparator('D:')).toBe('\\');
  });

  it('detects UNC shares as Windows and leaves POSIX double-slash alone', () => {
    expect(pathSeparator('\\\\server\\share')).toBe('\\');
    expect(pathSeparator('//server/share')).toBe('/');
  });

  it('treats POSIX absolute, home-relative and plain relative paths as POSIX', () => {
    expect(pathSeparator('/srv/app')).toBe('/');
    expect(pathSeparator('/')).toBe('/');
    expect(pathSeparator('~/app')).toBe('/');
    expect(pathSeparator('app')).toBe('/');
    expect(pathSeparator('app/sub')).toBe('/');
  });

  it('treats backslash-relative paths as Windows', () => {
    expect(pathSeparator('src\\main.ts')).toBe('\\');
    expect(pathSeparator('..\\app')).toBe('\\');
  });

  it('has no opinion about empty input', () => {
    expect(pathSeparator('')).toBe('/');
    expect(pathSeparator('   ')).toBe('/');
  });
});

describe('appendPathSeparator', () => {
  it('appends a backslash to Windows drive and backslash-relative paths', () => {
    expect(appendPathSeparator('D:\\project\\app')).toBe('D:\\project\\app\\');
    expect(appendPathSeparator('D:')).toBe('D:\\');
    expect(appendPathSeparator('\\\\server\\share')).toBe('\\\\server\\share\\');
    expect(appendPathSeparator('src\\nested')).toBe('src\\nested\\');
  });

  it('appends a forward slash to POSIX paths', () => {
    expect(appendPathSeparator('/home/user/app')).toBe('/home/user/app/');
    expect(appendPathSeparator('/')).toBe('/');
    expect(appendPathSeparator('~/project')).toBe('~/project/');
  });

  it('never doubles an existing trailing separator', () => {
    expect(appendPathSeparator('D:\\project\\')).toBe('D:\\project\\');
    expect(appendPathSeparator('D:\\project/')).toBe('D:\\project/');
    expect(appendPathSeparator('/home/user/')).toBe('/home/user/');
    expect(appendPathSeparator('//server/share/')).toBe('//server/share/');
  });

  it('keeps relative paths relative', () => {
    expect(appendPathSeparator('app')).toBe('app/');
    expect(appendPathSeparator('app/sub')).toBe('app/sub/');
    expect(appendPathSeparator('')).toBe('');
  });

  it('preserves a forward-slash drive spelling', () => {
    expect(appendPathSeparator('C:/Users/me')).toBe('C:/Users/me/');
    expect(isWindowsPath('C:/Users/me')).toBe(false);
  });
});
