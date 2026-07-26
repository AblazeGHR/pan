import { useState } from 'react';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';

interface ToolGroupProps {
  items: Message[];
}

function toolName(content: string): string {
  if (!content) return '(empty)';
  const callMatch = content.match(/^tool call:\s*(.+)/);
  if (callMatch?.[1]) return callMatch[1].split('\n')[0]?.trim() || '';
  const resultMatch = content.match(/^tool result \(([^)]+)\)/);
  if (resultMatch) return resultMatch[1]!.trim();
  const idx = content.indexOf('(');
  if (idx >= 0) return content.slice(0, idx).trim();
  return content.split('\n')[0]!.trim().slice(0, 30);
}

function formatToolContent(content: string): string {
  if (!content) return '🔧 (empty)';

  // Legacy format: "tool call: name\nargs: {...}"
  let match = content.match(/^tool call:\s*(.+?)(?:\r?\n|\r)args:\s*([\s\S]*)$/);
  if (match) {
    const name = match[1]!.trim();
    const jsonText = match[2]!.trim();
    return `🔧 ${name}\n${formatJson(jsonText)}`;
  }

  // Modern format: "name(args)"
  match = content.match(/^([^(]+)\(([\s\S]*)\)$/);
  if (match) {
    const name = (match[1] || '').trim() || 'tool';
    const jsonText = match[2] || '';
    return `🔧 ${name}\n${formatJson(jsonText)}`;
  }

  return `🔧 ${content}`;
}

function formatJson(jsonText: string): string {
  try {
    const parsed = JSON.parse(jsonText);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const cleaned: Record<string, unknown> = {};
      for (const key of Object.keys(parsed)) {
        if (key === '_comment' || key === '$comment' || key === '-comment') continue;
        cleaned[key] = parsed[key];
      }
      return JSON.stringify(cleaned, null, 2);
    }
    return jsonText;
  } catch {
    return jsonText;
  }
}

export function ToolGroup({ items }: ToolGroupProps) {
  const [isOpen, setIsOpen] = useState(false);
  const unread = useSessionStore((s) => s.getUnread());
  const hasUnread = items.some((t) => unread.has(t.content));
  const names = items
    .map((t) => toolName(t.content))
    .slice(0, 3)
    .join(', ');
  const moreCount = items.length > 3 ? `, +${items.length - 3}` : '';

  return (
    <div className={`tool-group ${isOpen ? '' : 'collapsed'}`}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 text-xs text-text-secondary hover:text-text-primary transition-colors w-full text-left"
      >
        <span className="text-warning">🔧</span>
        <span>
          <strong>{items.length} tools:</strong> {names}{moreCount}
        </span>
        <span className="text-[10px]">{isOpen ? '▲' : '▼'}</span>
        {hasUnread && !isOpen && (
          <span className="w-2 h-2 rounded-full bg-accent" title="unread" />
        )}
      </button>
      {isOpen && (
        <div className="mt-2 space-y-1">
          {items.map((tool, i) => (
            <pre
              key={i}
              className="p-2 rounded bg-bg-tertiary border border-border-muted text-xs text-text-secondary whitespace-pre-wrap"
            >
              {formatToolContent(tool.content)}
            </pre>
          ))}
        </div>
      )}
    </div>
  );
}
