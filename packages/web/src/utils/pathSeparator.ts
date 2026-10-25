/**
 * Separator handling for user-typed filesystem paths.
 *
 * The dashboard talks to one server, and that server runs on exactly one
 * platform. The browser therefore cannot use its own platform to join paths;
 * it must infer the style from the path the user is editing or from the
 * listing the server returned. A wrong guess (`/srv/app\`) reaches the server
 * as a directory that does not exist, so the rule is deliberately shape-based:
 *
 * - `D:\...`, `D:/...`, `D:` and UNC (`\\server\share`) are Windows spellings
 *   and keep the separator they were written with (backslash by default);
 * - every other path — POSIX absolute (`/srv/app`), home-relative (`~/app`)
 *   and plain relative (`app`) — uses `/`.
 */

export type PathSeparator = '\\' | '/';

/** Separator implied by the path's own spelling; never by the browser's OS. */
export function pathSeparator(path: string): PathSeparator {
  const value = path.trim();
  if (!value) return '/';
  // A drive root keeps the spelling the user typed: `C:/x` stays forward-slash.
  const driveRoot = /^[A-Za-z]:([\\/])/.exec(value);
  if (driveRoot) return driveRoot[1] as PathSeparator;
  // A bare drive (`D:`) has no separator yet; it is drive-relative in Windows
  // APIs and the UI treats it as the drive root.
  if (/^[A-Za-z]:$/.test(value)) return '\\';
  // UNC shares are Windows-only. `//server/share` is not UNC here: a POSIX
  // path may legitimately start with a double slash.
  if (value.startsWith('\\\\')) return '\\';
  // No drive or UNC root but a backslash inside: a Windows relative path
  // (`src\main.ts`) or a mixed-separator spelling.
  if (value.includes('\\')) return '\\';
  return '/';
}

export function isWindowsPath(path: string): boolean {
  return pathSeparator(path) === '\\';
}

/**
 * Append the path's own separator unless it already ends with one.
 *
 * Used when a directory listing entry is turned into "this directory" in the
 * text input. Returning the input unchanged for an already-terminated path
 * keeps double separators out of the request.
 */
export function appendPathSeparator(path: string): string {
  if (!path) return path;
  const last = path.charAt(path.length - 1);
  if (last === '\\' || last === '/') return path;
  return `${path}${pathSeparator(path)}`;
}
