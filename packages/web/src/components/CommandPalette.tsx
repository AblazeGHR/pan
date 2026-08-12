import { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionStore } from '@/stores/sessionStore';
import { useUIStore } from '@/stores/uiStore';
import { Search } from 'lucide-react';

interface PaletteAction {
  id: string;
  label: string;
  detail?: string;
  group: string;
  action: () => void;
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const navigate = useNavigate();
  const sessions = useSessionStore((s) => s.sessions);
  const selectSession = useSessionStore((s) => s.selectSession);
  const createNewSession = useSessionStore((s) => s.createNewSession);
  const {
    toggleSettings,
    toggleTheme,
    showToast,
  } = useUIStore();

  // Generate actions
  const actions = useMemo<PaletteAction[]>(() => {
    const result: PaletteAction[] = [];

    // Navigation
    result.push(
      { id: 'nav-chat', label: 'Chat', detail: 'Go to chat view', group: 'Navigation', action: () => navigate('/') },
      { id: 'nav-editor', label: 'Editor', detail: 'Go to editor view', group: 'Navigation', action: () => navigate('/editor') },
    );

    // Session
    result.push(
      { id: 'sess-new', label: 'New Session', detail: 'Create a new session', group: 'Session', action: () => {
        createNewSession(`session-${sessions.length + 1}`).catch((e) =>
          showToast(e.message || 'Creation failed', 'error'),
        );
      }},
      { id: 'sess-import', label: 'Import', detail: 'Import from cbc/kimi', group: 'Session', action: () => {
        // TODO: trigger import modal — for now, navigate to home
        navigate('/');
      }},
    );

    // Jump to session
    for (const s of sessions.slice(0, 20)) {
      if (s.id.startsWith('__pending_')) continue;
      result.push({
        id: `sess-${s.id}`,
        label: s.name || 'Untitled',
        detail: s.workdir || s.adapter || s.model || '',
        group: 'Sessions',
        action: () => {
          selectSession(s.id);
          navigate('/');
        },
      });
    }

    // Settings
    result.push(
      { id: 'set-model', label: 'Settings', detail: 'Open session settings', group: 'Settings', action: toggleSettings },
      { id: 'set-theme', label: 'Toggle Theme', detail: 'Switch dark/light mode', group: 'Settings', action: toggleTheme },
      { id: 'set-group', label: 'Toggle Sidebar', detail: 'Collapse/expand sidebar', group: 'Settings', action: () => {
        useUIStore.getState().toggleSidebar();
      }},
    );

    return result;
  }, [sessions, navigate, selectSession, createNewSession, toggleSettings, toggleTheme, showToast]);

  // Filter by query
  const filtered = useMemo(() => {
    if (!query.trim()) return actions;
    const q = query.toLowerCase();
    return actions.filter(
      (a) =>
        a.label.toLowerCase().includes(q) ||
        a.detail?.toLowerCase().includes(q) ||
        a.group.toLowerCase().includes(q),
    );
  }, [query, actions]);

  // Group filtered results
  const grouped = useMemo(() => {
    const map = new Map<string, PaletteAction[]>();
    for (const a of filtered) {
      const existing = map.get(a.group) || [];
      existing.push(a);
      map.set(a.group, existing);
    }
    return [...map.entries()];
  }, [filtered]);

  // Reset on open/close
  useEffect(() => {
    if (open) {
      setQuery('');
      setSelectedIndex(0);
      inputRef.current?.focus();
    }
  }, [open]);

  // Keyboard listener
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
      if (e.key === 'Escape' && open) {
        setOpen(false);
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const action = filtered[selectedIndex];
      if (action) {
        action.action();
        setOpen(false);
      }
    }
  };

  let globalIndex = 0;

  // Auto-focus input
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh]"
      onClick={() => setOpen(false)}
    >
      <div className="fixed inset-0 bg-black/50" />
      <div
        className="relative w-full max-w-lg bg-bg-tertiary border border-border-default rounded-lg shadow-dropdown overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Input */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border-default">
          <Search size={16} className="text-text-tertiary shrink-0" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            onKeyDown={handleKeyDown}
            placeholder="Search commands, sessions..."
            className="flex-1 bg-transparent border-none outline-none text-sm text-text-primary placeholder:text-text-tertiary"
          />
          <kbd className="text-[10px] text-text-tertiary bg-bg-secondary px-1.5 py-0.5 rounded border border-border-default">
            ESC
          </kbd>
        </div>

        {/* Results */}
        <div className="max-h-64 overflow-y-auto py-1">
          {grouped.map(([group, items]) => (
            <div key={group}>
              <div className="px-4 py-1 text-[10px] text-text-tertiary font-medium uppercase tracking-wider">
                {group}
              </div>
              {items.map((action) => {
                const idx = globalIndex++;
                return (
                  <button
                    key={action.id}
                    className={`w-full flex items-center gap-3 px-4 py-1.5 text-sm text-left transition-colors ${
                      idx === selectedIndex
                        ? 'bg-accent/20 text-text-primary'
                        : 'text-text-primary hover:bg-bg-hover/50'
                    }`}
                    onMouseEnter={() => setSelectedIndex(idx)}
                    onClick={() => {
                      action.action();
                      setOpen(false);
                    }}
                  >
                    <span className="flex-1">{action.label}</span>
                    {action.detail && (
                      <span className="text-xs text-text-tertiary truncate max-w-[200px]">
                        {action.detail}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
          {filtered.length === 0 && (
            <div className="px-4 py-4 text-sm text-text-tertiary text-center">
              No results
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
