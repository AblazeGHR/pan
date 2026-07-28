import { useRef, useCallback, useEffect } from 'react';
import { useUIStore } from '@/stores/uiStore';

export function SidebarResizer() {
  const sidebarWidth = useUIStore((s) => s.sidebarWidth);
  const setSidebarWidth = useUIStore((s) => s.setSidebarWidth);
  const dragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);
  const lastClick = useRef(0);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      const now = Date.now();
      if (now - lastClick.current < 300) {
        lastClick.current = 0;
        setSidebarWidth(260);
        return;
      }
      lastClick.current = now;

      dragging.current = true;
      startX.current = e.clientX;
      startWidth.current = sidebarWidth;
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      e.preventDefault();
    },
    [sidebarWidth, setSidebarWidth],
  );

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      const delta = e.clientX - startX.current;
      setSidebarWidth(startWidth.current + delta);
    };

    const onMouseUp = () => {
      if (dragging.current) {
        dragging.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    return () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
  }, [setSidebarWidth]);

  return (
    <div
      className="absolute top-0 right-0 h-full w-[4px] cursor-col-resize z-10 hover:bg-accent/30 transition-colors group"
      onMouseDown={handleMouseDown}
    >
      <div className="absolute top-0 right-0 w-[2px] h-full bg-transparent group-hover:bg-accent/40 transition-colors" />
    </div>
  );
}
