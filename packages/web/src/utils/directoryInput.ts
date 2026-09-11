import type { DirectoryListResponse } from '@/services/api';

export interface DirectoryInputParts {
  input: string;
  directory: string;
  search: string;
  candidate: string;
  hasSeparator: boolean;
}

/**
 * Split a directory input at the final Windows separator.
 *
 * The server remains authoritative for path existence and permissions. This
 * function only preserves the user's spelling while identifying the parent
 * directory used for one-level search; it never attempts to make an invalid
 * path valid by guessing a different root.
 */
export function parseDirectoryInput(raw: string): DirectoryInputParts {
  const input = raw.trim();
  if (!input) {
    return { input: '', directory: '', search: '', candidate: '', hasSeparator: false };
  }

  if (/^[A-Za-z]:$/.test(input)) {
    return {
      input,
      directory: `${input}\\`,
      search: '',
      candidate: `${input}\\`,
      hasSeparator: true,
    };
  }

  const windowsSeparator = input.lastIndexOf('\\');
  const fallbackSeparator = input.lastIndexOf('/');
  const separatorIndex = windowsSeparator >= 0 ? windowsSeparator : fallbackSeparator;
  if (separatorIndex < 0) {
    return { input, directory: '', search: input, candidate: input, hasSeparator: false };
  }

  let directory = input.slice(0, separatorIndex);
  // `D:\foo` is a drive-relative spelling in Windows APIs. The input
  // contract means it refers to the drive root in this UI.
  if (/^[A-Za-z]:$/.test(directory)) directory += '\\';
  // A leading separator is a valid root-relative path. Keep the root rather
  // than turning it into an empty search base.
  if (!directory && separatorIndex === 0) directory = input.charAt(0);

  return {
    input,
    directory,
    search: input.slice(separatorIndex + 1),
    candidate: input,
    hasSeparator: true,
  };
}

export function parentDirectory(path: string): string {
  const input = path.trim();
  const parts = parseDirectoryInput(input);
  if (!parts.hasSeparator) return '';
  return parts.directory;
}

export function samePath(left: string, right: string): boolean {
  return left.replace(/[\\/]+$/, '').toLowerCase() === right.replace(/[\\/]+$/, '').toLowerCase();
}

export function isMissingDirectoryError(error: unknown): boolean {
  return error instanceof Error && /HTTP (?:400|404)\b/.test(error.message);
}

export type DirectoryFetcher = (
  path?: string,
  includeFiles?: boolean,
) => Promise<DirectoryListResponse>;

/** Re-list the file's parent immediately before it is submitted. */
export async function directoryEntryExists(
  path: string,
  fetcher: DirectoryFetcher,
): Promise<boolean> {
  const parent = parentDirectory(path);
  if (!parent) return false;
  const listing = await fetcher(parent, true);
  return listing.entries.some((entry) => !entry.isDirectory && samePath(entry.path, path));
}
