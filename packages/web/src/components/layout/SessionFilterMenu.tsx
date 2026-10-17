import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, ListFilter } from 'lucide-react';
import { useShallow } from 'zustand/react/shallow';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { SPECIAL_FILTERS, collectAdapterOptions } from '@/utils/sessionFilters';

/** Menu width (Tailwind w-64). Also the clamp basis for narrow viewports. */
const MENU_WIDTH = 256;
const VIEWPORT_MARGIN = 8;
const MIN_MENU_HEIGHT = 160;

type FilterGroupId = 'adapter' | 'state';

interface MenuPosition {
  left: number;
  top: number;
  maxHeight: number;
}

/**
 * Session filter menu: a viewport-constrained, internally scrollable popover
 * with two collapsible second-level groups.
 *
 * - Adapter: one checkbox per adapter name present on the live session list
 *   (derived from real data — never a hardcoded registry). Selections inside
 *   the group are OR-ed.
 * - State: the pre-existing special filters, with their AND semantics kept
 *   exactly as before.
 * - Across groups: AND (handled by utils/sessionFilters).
 *
 * Interaction guarantees:
 * - Expanding/collapsing a group never clears its checkmarks (selection lives
 *   in the UI store, expansion in local state).
 * - Checking an item never closes the menu.
 * - Escape and outside clicks close it; the trigger and every item stay
 *   keyboard reachable (native buttons/checkboxes, aria-expanded, checkbox
 *   roles).
 * - The panel is clamped to the viewport (fixed positioning + measured
 *   trigger rect), so narrow screens never overflow and a long menu scrolls
 *   internally with its last item reachable.
 */
export function SessionFilterMenu() {
  const sessions = useSessionStore((s) => s.sessions);
  const {
    specialFilters,
    adapterFilters,
    toggleSpecialFilter,
    toggleAdapterFilter,
    clearAllFilters,
  } = useUIStore(useShallow((s) => ({
    specialFilters: s.specialFilters,
    adapterFilters: s.adapterFilters,
    toggleSpecialFilter: s.toggleSpecialFilter,
    toggleAdapterFilter: s.toggleAdapterFilter,
    clearAllFilters: s.clearAllFilters,
  })));

  const [open, setOpen] = useState(false);
  // State starts expanded so the pre-existing filters stay one click away
  // (the historical flat list); Adapter starts collapsed and expands on click.
  const [expandedGroups, setExpandedGroups] = useState<Record<FilterGroupId, boolean>>({
    adapter: false,
    state: true,
  });
  const [position, setPosition] = useState<MenuPosition | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const adapterOptions = useMemo(() => collectAdapterOptions(sessions), [sessions]);
  const activeCount = specialFilters.size + adapterFilters.size;

  const toggleGroup = useCallback((group: FilterGroupId) => {
    setExpandedGroups((prev) => ({ ...prev, [group]: !prev[group] }));
  }, []);

  const closeMenu = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  // ── Viewport-constrained placement ──
  // Fixed positioning relative to the measured trigger rect, clamped into the
  // viewport: narrow screens cannot overflow horizontally, and the max height
  // always leaves the panel (and its last item) inside the window.
  const reposition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger || typeof window === 'undefined') return;
    const rect = trigger.getBoundingClientRect();
    const maxLeft = Math.max(VIEWPORT_MARGIN, window.innerWidth - MENU_WIDTH - VIEWPORT_MARGIN);
    const left = Math.min(Math.max(VIEWPORT_MARGIN, rect.left), maxLeft);
    const top = rect.bottom + 4;
    const maxHeight = Math.max(MIN_MENU_HEIGHT, window.innerHeight - top - VIEWPORT_MARGIN);
    setPosition({ left, top, maxHeight });
  }, []);

  useLayoutEffect(() => {
    if (!open) {
      setPosition(null);
      return;
    }
    reposition();
    const onViewportChange = () => reposition();
    window.addEventListener('resize', onViewportChange);
    // Capture phase: any scroll (the page, the sidebar list, the drawer) must
    // keep the panel anchored to its trigger.
    window.addEventListener('scroll', onViewportChange, true);
    return () => {
      window.removeEventListener('resize', onViewportChange);
      window.removeEventListener('scroll', onViewportChange, true);
    };
  }, [open, reposition]);

  // Escape closes from anywhere (document level), plus a menu-level handler so
  // the event never leaks to ancestor handlers.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        closeMenu();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [open, closeMenu]);

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1 p-1 rounded transition-colors ${
          activeCount > 0
            ? 'text-accent bg-accent/10'
            : 'text-text-tertiary hover:text-text-primary'
        }`}
        title="Special filters"
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid="session-filter-trigger"
      >
        <ListFilter size={14} />
        {activeCount > 0 && (
          <span className="text-[10px] leading-none font-medium" data-testid="session-filter-count">
            {activeCount}
          </span>
        )}
      </button>
      {open && position && (
        <>
          {/* Click-away backdrop */}
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          {/* z-40：本层必须压在 WorkspaceRail 之上。rail 外壳是 `relative z-30`
              的兄弟堆叠上下文，而 <aside> 是 `relative` + z-auto（不成堆叠上下文），
              于是两者同落在 App 的 `relative z-30` 外壳里；同为 30 时按 DOM 顺序
              决胜，后出现的 rail 面板（bg-bg-secondary，不透明）会盖住本层伸入
              rail 的右半部分（w-64 从筛选按钮左沿起算，宽于侧栏）。提到 40 即可
              在该上下文内胜出，且仍受该上下文约束，不会盖住挂在 <body> 的
              z-40/z-50 portal Modal。固定定位同样受该祖先堆叠上下文约束。 */}
          <div
            role="menu"
            aria-label="Session filters"
            data-testid="session-filter-menu"
            className="fixed z-40 flex w-64 flex-col overflow-hidden rounded border border-border-default bg-bg-primary shadow-lg"
            style={{ left: position.left, top: position.top, maxHeight: position.maxHeight }}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.stopPropagation();
                closeMenu();
              }
            }}
          >
            {/* Only the group list scrolls; the Clear action stays pinned. */}
            <div className="min-h-0 flex-1 overflow-y-auto py-1">
              {/* ── Adapter group (OR within the group) ── */}
              <div role="group" aria-labelledby="session-filter-group-adapter">
                <button
                  type="button"
                  role="menuitem"
                  id="session-filter-group-adapter"
                  aria-expanded={expandedGroups.adapter}
                  aria-controls="session-filter-adapter-items"
                  data-testid="session-filter-group-adapter"
                  onClick={() => toggleGroup('adapter')}
                  className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left hover:bg-bg-hover/40"
                >
                  {expandedGroups.adapter ? (
                    <ChevronDown size={12} className="shrink-0 text-text-tertiary" />
                  ) : (
                    <ChevronRight size={12} className="shrink-0 text-text-tertiary" />
                  )}
                  <span className="text-xs font-medium text-text-primary">Adapter</span>
                  {adapterFilters.size > 0 && (
                    <span className="ml-auto text-[10px] leading-none text-text-tertiary">
                      {adapterFilters.size}
                    </span>
                  )}
                </button>
                {expandedGroups.adapter && (
                  <div id="session-filter-adapter-items">
                    {adapterOptions.length === 0 ? (
                      <p className="px-3 py-1.5 text-[10px] text-text-tertiary">
                        No adapters in the session list
                      </p>
                    ) : (
                      adapterOptions.map((adapter) => (
                        <label
                          key={adapter}
                          role="menuitemcheckbox"
                          aria-checked={adapterFilters.has(adapter)}
                          className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-bg-hover/40 select-none"
                        >
                          <input
                            type="checkbox"
                            checked={adapterFilters.has(adapter)}
                            onChange={() => toggleAdapterFilter(adapter)}
                            className="accent-accent shrink-0"
                          />
                          <span className="text-xs text-text-primary">{adapter}</span>
                        </label>
                      ))
                    )}
                  </div>
                )}
              </div>

              {/* ── State group (existing special filters, AND semantics) ── */}
              <div
                role="group"
                aria-labelledby="session-filter-group-state"
                className="border-t border-border-muted"
              >
                <button
                  type="button"
                  role="menuitem"
                  id="session-filter-group-state"
                  aria-expanded={expandedGroups.state}
                  aria-controls="session-filter-state-items"
                  data-testid="session-filter-group-state"
                  onClick={() => toggleGroup('state')}
                  className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left hover:bg-bg-hover/40"
                >
                  {expandedGroups.state ? (
                    <ChevronDown size={12} className="shrink-0 text-text-tertiary" />
                  ) : (
                    <ChevronRight size={12} className="shrink-0 text-text-tertiary" />
                  )}
                  <span className="text-xs font-medium text-text-primary">State</span>
                  {specialFilters.size > 0 && (
                    <span className="ml-auto text-[10px] leading-none text-text-tertiary">
                      {specialFilters.size}
                    </span>
                  )}
                </button>
                {expandedGroups.state && (
                  <div id="session-filter-state-items">
                    {SPECIAL_FILTERS.map((f) => (
                      <label
                        key={f.id}
                        role="menuitemcheckbox"
                        aria-checked={specialFilters.has(f.id)}
                        className="flex items-start gap-2 px-3 py-1.5 cursor-pointer hover:bg-bg-hover/40 select-none"
                      >
                        <input
                          type="checkbox"
                          checked={specialFilters.has(f.id)}
                          onChange={() => toggleSpecialFilter(f.id)}
                          className="mt-0.5 accent-accent shrink-0"
                        />
                        <span className="min-w-0">
                          <span className="block text-xs text-text-primary">{f.label}</span>
                          <span className="block text-[10px] text-text-tertiary leading-tight">{f.description}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            </div>
            {activeCount > 0 && (
              <button
                onClick={clearAllFilters}
                className="w-full shrink-0 text-left px-3 py-1.5 text-xs text-text-tertiary hover:text-text-primary border-t border-border-muted"
              >
                Clear filters
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
