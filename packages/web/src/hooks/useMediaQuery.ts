import { useState, useEffect } from 'react';

/**
 * Detects mobile viewport. Matches old app.ts UAParser logic.
 * Mobile is considered < 768px (Tailwind md breakpoint).
 */
export function useMediaQuery() {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const query = window.matchMedia('(max-width: 767px)');
    setIsMobile(query.matches);

    const handler = (e: MediaQueryListEvent) => {
      setIsMobile(e.matches);
    };
    query.addEventListener('change', handler);
    return () => query.removeEventListener('change', handler);
  }, []);

  return { isMobile };
}
