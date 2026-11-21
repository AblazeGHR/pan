import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Maximize2, Minimize2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';

export function RecordWorkspace({ children, recordKey, detail, onToggleView, editing = false, flow = false }: {
  children: ReactNode;
  recordKey?: string;
  detail?: ReactNode;
  editing?: boolean;
  flow?: boolean;
  onToggleView?: () => boolean;
}) {
  const [expandedKey, setExpandedKey] = useState<string>();
  const expanded = !!recordKey && (editing || expandedKey === recordKey);
  const pane = useRef<HTMLElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const layoutId = useId();

  useLayoutEffect(() => {
    setExpandedKey(undefined);
    if (!recordKey) return;
    previouslyFocused.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    pane.current?.focus({ preventScroll: true });
    return () => {
      const trigger = previouslyFocused.current;
      if (trigger?.isConnected && trigger.closest('[hidden]') === null) trigger.focus({ preventScroll: true });
    };
  }, [recordKey]);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col lg:flex-row">
      <div className={expanded ? 'hidden' : `flex min-h-0 min-w-0 flex-1 flex-col ${recordKey ? 'max-lg:hidden' : ''}`}>
        {children}
      </div>
      {recordKey && (
        <aside
          ref={pane}
          tabIndex={-1}
          aria-label="Record workspace"
          data-layout={expanded ? 'expanded' : 'side'}
          className={`flex min-h-0 min-w-0 flex-col overflow-hidden bg-bg-primary outline-none ${expanded ? 'flex-1' : 'w-full flex-1 lg:w-[min(46%,36rem)] lg:flex-none lg:border-l lg:border-border-default'}`}
        >
          {!editing && <div className="flex shrink-0 items-center justify-end border-b border-border-muted px-3 py-1">
            <Button size="sm" variant="ghost" aria-controls={layoutId} aria-expanded={expanded} onClick={() => {
              if (onToggleView?.() === false) return;
              setExpandedKey(expanded ? undefined : recordKey);
            }}>
              {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              {expanded ? 'Return to side detail' : 'Expand detail'}
            </Button>
          </div>}
          <div id={layoutId} className={flow ? "min-w-0" : "min-h-0 flex-1 overflow-y-auto"}>{detail}</div>
        </aside>
      )}
    </div>
  );
}
