import { useCallback, useEffect, useRef, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { ChevronLeft, ChevronRight, Code, ListChecks, MessageSquare, TerminalSquare } from 'lucide-react';

// All view entries belong here; every three entries form one page.
const entries = [
  { to: '/', label: 'Chat', icon: MessageSquare, end: true },
  { to: '/editor', label: 'Editor', icon: Code },
  { to: '/jobs', label: 'Jobs', icon: ListChecks },
  { to: '/terminals', label: 'Terminal', icon: TerminalSquare },
];
const groups = Array.from({ length: Math.ceil(entries.length / 3) }, (_, group) =>
  Array.from({ length: 3 }, (_, slot) => entries[group * 3 + slot]));

export function ViewEntryGroups({ isMobile }: { isMobile: boolean }) {
  const { pathname } = useLocation();
  const routeGroup = Math.max(0, Math.floor(entries.findIndex((entry) =>
    entry.end ? pathname === entry.to : pathname === entry.to || pathname.startsWith(`${entry.to}/`)) / 3));
  const [selection, setSelection] = useState({ pathname, group: routeGroup });
  // Reveal a changed route once; browsing pages never navigates or reasserts the route.
  if (selection.pathname !== pathname) setSelection({ pathname, group: routeGroup });
  const group = selection.pathname === pathname ? selection.group : routeGroup;
  const gesture = useRef<{ id: number; x: number; y: number; axis: 'pending' | 'horizontal' | 'vertical' } | null>(null);
  const suppressClickUntil = useRef(0);
  const navRef = useRef<HTMLElement>(null);
  const step = useCallback((direction: number) => setSelection((previous) => ({
    pathname, group: Math.max(0, Math.min(groups.length - 1, previous.group + direction)),
  })), [pathname]);

  useEffect(() => {
    const nav = navRef.current;
    if (isMobile || !nav) return;
    // One same-direction burst is one operation, including its inertia tail.
    // Silence rearms it; reversing direction starts a new operation immediately.
    let lastTime = -Infinity;
    let lastDirection = 0;
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey || event.deltaY === 0 || !event.cancelable) return;
      event.preventDefault();
      const direction = Math.sign(event.deltaY);
      const now = performance.now();
      if (direction !== lastDirection || now - lastTime >= 200) step(direction);
      lastDirection = direction;
      lastTime = now;
    };
    nav.addEventListener('wheel', onWheel, { passive: false });
    return () => nav.removeEventListener('wheel', onWheel);
  }, [isMobile, step]);

  return <nav ref={navRef} aria-label="界面入口" className="flex min-w-0 items-center gap-1">
    {!isMobile && <button type="button" aria-label="上一组界面入口" disabled={group === 0}
      className="shrink-0 w-4 flex justify-center text-text-tertiary disabled:opacity-30" onClick={() => step(-1)}>
      <ChevronLeft size={14} />
    </button>}
    <div className="min-w-0 flex-1 overflow-hidden" style={{ touchAction: 'pan-y pinch-zoom' }}
      tabIndex={0} aria-label={`界面入口，第 ${group + 1} 组，共 ${groups.length} 组`}
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault(); step(event.key === 'ArrowLeft' ? -1 : 1);
        }
      }}
      onClickCapture={(event) => {
        if (event.detail !== 0 && Date.now() < suppressClickUntil.current) {
          event.preventDefault(); event.stopPropagation();
        }
      }}
      onPointerDown={(event) => {
        if (!event.isPrimary || event.button !== 0 || gesture.current) return;
        gesture.current = { id: event.pointerId, x: event.clientX, y: event.clientY, axis: 'pending' };
      }}
      onPointerMove={(event) => {
        const start = gesture.current;
        if (!start || start.id !== event.pointerId || start.axis !== 'pending') return;
        const dx = Math.abs(event.clientX - start.x), dy = Math.abs(event.clientY - start.y);
        if (Math.max(dx, dy) < 10) return;
        if (dx > dy * 1.5) {
          start.axis = 'horizontal';
          event.currentTarget.setPointerCapture(event.pointerId);
        } else if (dy >= dx) start.axis = 'vertical';
      }}
      onPointerUp={(event) => {
        const start = gesture.current;
        if (!start || start.id !== event.pointerId) return;
        gesture.current = null;
        const dx = event.clientX - start.x, dy = event.clientY - start.y;
        if (start.axis === 'horizontal') {
          suppressClickUntil.current = Date.now() + 400;
          if (Math.abs(dx) >= 40 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
        }
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onPointerCancel={() => { gesture.current = null; }}
      onLostPointerCapture={() => { gesture.current = null; }}
      onPointerLeave={() => { if (gesture.current?.axis === 'pending') gesture.current = null; }}>
      {/* Inputs during animation retarget the next whole page; moves never change pages. */}
      <div className="flex transition-transform duration-200 ease-out motion-reduce:transition-none"
        style={{ transform: `translateX(-${group * 100}%)` }}>
        {groups.map((page, index) => <div key={index} inert={index !== group}
          aria-hidden={index !== group} className="grid w-full min-w-0 shrink-0 grid-cols-3">
          {page.map((entry, slot) => entry ? <NavLink key={entry.to} to={entry.to} end={entry.end}
            aria-label={entry.label} draggable={false}
            className={({ isActive }) => `min-w-0 flex items-center justify-center gap-1 py-1.5 text-xs rounded transition-colors ${
              isActive ? 'bg-accent/20 text-accent font-medium' : 'text-text-tertiary hover:text-text-secondary hover:bg-bg-hover'}`}>
            <entry.icon size={12} className="shrink-0" /><span className="truncate">{entry.label}</span>
          </NavLink> : <div key={slot} aria-hidden="true" />)}
        </div>)}
      </div>
    </div>
    {!isMobile && <button type="button" aria-label="下一组界面入口" disabled={group === groups.length - 1}
      className="shrink-0 w-4 flex justify-center text-text-tertiary disabled:opacity-30" onClick={() => step(1)}>
      <ChevronRight size={14} />
    </button>}
  </nav>;
}
