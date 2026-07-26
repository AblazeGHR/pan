import { useRef } from 'react';

const CACHE_MAX = 2000;
const cache = new Map<string, string>();

/**
 * Memoized markdown rendering using react-markdown is handled by
 * MarkdownRenderer.tsx component. This hook provides caching utilities.
 */
export function useMarkdown() {
  const cacheRef = useRef(cache);

  const getCached = (text: string): string | null => {
    return cacheRef.current.get(text) ?? null;
  };

  const setCache = (text: string, html: string): void => {
    const c = cacheRef.current;
    c.set(text, html);
    if (c.size > CACHE_MAX) {
      const first = c.keys().next().value;
      if (first) c.delete(first);
    }
  };

  return { getCached, setCache };
}
