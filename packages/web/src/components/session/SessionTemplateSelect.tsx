import { createPortal } from 'react-dom';
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { SessionTemplate } from '@/types';

interface SessionTemplateSelectProps {
  templates: SessionTemplate[];
  value: string;
  onChange: (value: string) => void;
  labelId: string;
  disabled?: boolean;
}

function manifestLabel(template: SessionTemplate): string {
  if (template.sourceManifestLabel) return template.sourceManifestLabel;
  if (template.sourceManifest) {
    const parts = template.sourceManifest.replace(/\\/g, '/').split('/').filter(Boolean);
    return `${parts[parts.length - 1] || ''}/manifest.json`;
  }
  return 'manifest.json';
}

function templateLabel(template: SessionTemplate): string {
  return `${template.name} (${template.model || '?'})${template.mcpServers?.length ? ' [MCP]' : ''} (${manifestLabel(template)})`;
}

/** Searchable, keyboard-operable picker for Session templates. Search is local
 * to the open list and never changes the controlled selection by itself. */
export function SessionTemplateSelect({ templates, value, onChange, labelId, disabled = false }: SessionTemplateSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const [position, setPosition] = useState<{ left: number; top?: number; bottom?: number; width: number; maxHeight: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const listId = useId();
  const searchId = useId();

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    const items = templates.filter((template) => {
      if (!needle) return true;
      return [template.name, template.adapter, template.model, manifestLabel(template)]
        .some((part) => (part || '').toLocaleLowerCase().includes(needle));
    });
    return [{ value: '', label: 'None', searchText: 'none clear' }, ...items.map((template) => ({
      value: template.name,
      label: templateLabel(template),
      searchText: `${template.name} ${template.adapter || ''} ${template.model || ''} ${manifestLabel(template)}`.toLocaleLowerCase(),
    }))].filter((item) => !needle || item.label.toLocaleLowerCase().includes(needle) || item.searchText.includes(needle));
  }, [query, templates]);

  const updatePosition = () => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const margin = 8;
    const width = Math.min(Math.max(rect.width, 220), Math.max(0, window.innerWidth - margin * 2));
    const left = Math.min(Math.max(margin, rect.left), Math.max(margin, window.innerWidth - width - margin));
    const below = Math.max(0, window.innerHeight - rect.bottom - margin - 4);
    const above = Math.max(0, rect.top - margin - 4);
    const opensUp = below < above && below < 200;
    const maxHeight = Math.min(320, opensUp ? above : below);
    setPosition(opensUp
      ? { left, bottom: window.innerHeight - rect.top + 4, width, maxHeight }
      : { left, top: rect.bottom + 4, width, maxHeight });
  };

  useLayoutEffect(() => {
    if (!open) {
      setPosition(null);
      return;
    }
    updatePosition();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
    const update = () => updatePosition();
    const onMouseDown = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!rootRef.current?.contains(target) && !target.closest('[data-session-template-menu]')) close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        close();
        triggerRef.current?.focus();
      }
    };
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [open]);

  useEffect(() => {
    setActiveIndex((current) => Math.min(current, Math.max(0, filtered.length - 1)));
    optionRefs.current = [];
  }, [filtered.length, query]);

  useEffect(() => {
    if (open) optionRefs.current[activeIndex]?.scrollIntoView?.({ block: 'nearest' });
  }, [activeIndex, open]);

  function close() {
    setOpen(false);
    setQuery('');
    setActiveIndex(0);
  }

  function select(next: string) {
    onChange(next);
    close();
  }

  function moveActive(delta: number) {
    setActiveIndex((current) => Math.min(filtered.length - 1, Math.max(0, current + delta)));
  }

  const selectedTemplate = templates.find((template) => template.name === value);
  const selectedLabel = selectedTemplate ? templateLabel(selectedTemplate) : 'None';

  return (
    <div ref={rootRef} className="w-full min-w-0">
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-labelledby={labelId}
        aria-controls={listId}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-activedescendant={open && filtered[activeIndex] ? `${listId}-option-${activeIndex}` : undefined}
        disabled={disabled}
        onClick={() => {
          if (open) close();
          else { setQuery(''); setActiveIndex(0); setOpen(true); }
        }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            if (!open) { setQuery(''); setActiveIndex(0); setOpen(true); }
            else if (event.key === 'ArrowDown') moveActive(1);
            else select(filtered[activeIndex]?.value ?? '');
          } else if (event.key === 'ArrowUp' && open) {
            event.preventDefault(); moveActive(-1);
          } else if (event.key === 'Escape' && open) {
            event.preventDefault(); event.stopPropagation(); close();
          }
        }}
        className="flex w-full min-w-0 items-center justify-between gap-2 rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-left text-sm text-text-primary outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-60"
      >
        <span className="min-w-0 truncate">{selectedLabel}</span>
        <span className="shrink-0 text-text-tertiary" aria-hidden>▾</span>
      </button>

      {open && position && createPortal(
        <div
          data-session-template-menu
          style={{ position: 'fixed', left: position.left, top: position.top, bottom: position.bottom, width: position.width, maxHeight: position.maxHeight }}
          className="z-[60] flex max-w-[calc(100vw-1rem)] flex-col overflow-hidden rounded border border-border-default bg-bg-secondary shadow-xl"
        >
          <input
            ref={searchRef}
            id={searchId}
            type="search"
            value={query}
            onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); }}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') { event.preventDefault(); moveActive(1); }
              else if (event.key === 'ArrowUp') { event.preventDefault(); moveActive(-1); }
              else if (event.key === 'Enter') { event.preventDefault(); if (filtered[activeIndex]) select(filtered[activeIndex].value); }
            }}
            placeholder="搜索名称、adapter、model 或 manifest…"
            aria-label="搜索 Session Template"
            aria-controls={listId}
            className="w-full shrink-0 border-b border-border-muted bg-bg-tertiary px-3 py-2 text-sm text-text-primary outline-none placeholder:text-text-tertiary"
          />
          <ul id={listId} role="listbox" aria-labelledby={labelId} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            {filtered.length === 0 ? (
              <li className="px-3 py-2 text-sm text-text-tertiary">没有匹配的 Session Template</li>
            ) : filtered.map((item, index) => (
              <li key={item.value || '__none'}>
                <button
                  ref={(element) => { optionRefs.current[index] = element; }}
                  id={`${listId}-option-${index}`}
                  type="button"
                  role="option"
                  aria-selected={item.value === value}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => select(item.value)}
                  className={`block w-full break-words px-3 py-2 text-left text-sm hover:bg-bg-tertiary ${item.value === value ? 'bg-accent/10 text-accent' : 'text-text-primary'} ${index === activeIndex ? 'outline outline-1 -outline-offset-1 outline-accent/50' : ''}`}
                >
                  {item.label}
                </button>
              </li>
            ))}
          </ul>
        </div>,
        document.body,
      )}
    </div>
  );
}
