import { useLayoutEffect, useRef, type ReactNode, type PointerEvent } from 'react';
import { createPortal } from 'react-dom';

/** Viewport-level shell; unmounting on close discards all coordinates. */
export function HistorySearchPopup({ container, children, title, onClose }: {
  container?: HTMLElement | null; children: ReactNode; title: string; onClose: () => void;
}) {
  const shell = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; x: number; y: number; left: number; top: number } | null>(null);
  const place = (left: number, top: number) => {
    const element = shell.current;
    if (!element) return;
    const viewport = window.visualViewport;
    const x = viewport?.offsetLeft ?? 0, y = viewport?.offsetTop ?? 0;
    const width = viewport?.width ?? window.innerWidth, height = viewport?.height ?? window.innerHeight;
    element.style.maxWidth = `${Math.max(0, width-16)}px`;
    element.style.maxHeight = `${Math.max(0, height-16)}px`;
    element.style.left = `${Math.max(x+8, Math.min(left, x+width-element.offsetWidth-8))}px`;
    element.style.top = `${Math.max(y+8, Math.min(top, y+height-element.offsetHeight-8))}px`;
  };
  useLayoutEffect(() => {
    const anchor = container?.getBoundingClientRect();
    place((anchor?.right ?? window.innerWidth)-(shell.current?.offsetWidth ?? 430)-24, (anchor?.top ?? 0)+8);
    const clamp = () => {
      const rectangle = shell.current?.getBoundingClientRect();
      if (rectangle) place(rectangle.left, rectangle.top);
      drag.current = null;
    };
    const observer = new ResizeObserver(clamp);
    if (shell.current) observer.observe(shell.current);
    window.addEventListener('resize', clamp);
    window.visualViewport?.addEventListener('resize', clamp);
    window.visualViewport?.addEventListener('scroll', clamp);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', clamp);
      window.visualViewport?.removeEventListener('resize', clamp);
      window.visualViewport?.removeEventListener('scroll', clamp);
    };
  }, [container]);
  const start = (event: PointerEvent<HTMLDivElement>) => {
    if (!event.isPrimary || event.button !== 0) return;
    const rectangle = shell.current!.getBoundingClientRect();
    drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rectangle.left, top: rectangle.top };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  };
  return createPortal(<div ref={shell} className="history-search-floating" onKeyDown={(event) => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); }
  }}>
    <div className="history-search-drag-handle" onPointerDown={start}
      onPointerMove={(event) => {
        const active = drag.current;
        if (active?.id === event.pointerId) place(active.left+event.clientX-active.x, active.top+event.clientY-active.y);
      }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
      onLostPointerCapture={() => { drag.current = null; }}>{title} · Drag to move</div>
    {children}
    <div className="history-search-number-help">Current Session: 1 = latest occurrence · Enter number to jump</div>
  </div>, document.body);
}
