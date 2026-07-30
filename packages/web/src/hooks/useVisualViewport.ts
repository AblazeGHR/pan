import { useEffect } from 'react';

export function useVisualViewport() {
  useEffect(() => {
    const root = document.documentElement;

    const update = () => {
      const vv = window.visualViewport;
      if (!vv) return;
      root.style.setProperty('--app-height', `${vv.height}px`);
      root.style.setProperty('--app-top', `${vv.offsetTop}px`);
    };

    window.visualViewport?.addEventListener('resize', update);
    window.visualViewport?.addEventListener('scroll', update);
    update();

    return () => {
      window.visualViewport?.removeEventListener('resize', update);
      window.visualViewport?.removeEventListener('scroll', update);
    };
  }, []);
}
