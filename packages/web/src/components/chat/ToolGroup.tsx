import { useState } from 'react';
import { ChevronDown, ChevronUp, CircleCheck, CircleX, Loader2, Wrench } from 'lucide-react';
import type { Message } from '@/types';
import { useSessionStore } from '@/stores/sessionStore';
import { useDetailStore } from '@/stores/detailStore';

interface ToolGroupProps {
  items: Message[];
}

interface ToolInfo {
  name: string;
  status: 'done' | 'error' | 'running';
  args: Record<string, unknown> | null;
  argsPreview: string;
  rawContent: string;
}

function parseTool(content: string): ToolInfo {
  if (!content) {
    return { name: '(empty)', status: 'running', args: null, argsPreview: '', rawContent: content };
  }

  let name = '';
  let argsText = '';

  // Format: "tool call: name\nargs: {...}"
  const callMatch = content.match(/^tool call:\s*(.+?)(?:\r?\n|\r)args:\s*([\s\S]*)$/);
  if (callMatch) {
    name = callMatch[1]?.split('\n')[0]?.trim() || '';
    argsText = callMatch[2]?.trim() || '';
  } else {
    // Format: "name(args)"
    const modernMatch = content.match(/^([^(]+)\(([\s\S]*)\)$/);
    if (modernMatch) {
      name = (modernMatch[1] || '').trim() || 'tool';
      argsText = modernMatch[2] || '';
    } else {
      // Format: "tool result (name)"
      const resultMatch = content.match(/^tool result\s*\(([^)]+)\)/);
      if (resultMatch) {
        name = resultMatch[1]?.trim() || '';
      } else {
        const idx = content.indexOf('(');
        if (idx >= 0) {
          name = content.slice(0, idx).trim();
        } else {
          name = content.split('\n')[0]?.trim().slice(0, 30) || '';
        }
      }
    }
  }

  // Parse args JSON and extract first arg for preview
  let args: Record<string, unknown> | null = null;
  let argsPreview = '';
  if (argsText) {
    try {
      args = JSON.parse(argsText);
      if (args && typeof args === 'object' && !Array.isArray(args)) {
        const keys = Object.keys(args);
        if (keys.length > 0) {
          const firstKey = keys[0]!;
          const firstVal = args[firstKey];
          const valStr = typeof firstVal === 'string' ? firstVal : JSON.stringify(firstVal);
          argsPreview = `${firstKey}: ${valStr.length > 40 ? valStr.slice(0, 40) + '...' : valStr}`;
        }
      }
    } catch {
      argsPreview = argsText.length > 30 ? argsText.slice(0, 30) + '...' : argsText;
    }
  }

  // Determine status
  let status: ToolInfo['status'] = 'running';
  const lower = content.toLowerCase();
  if (lower.includes('error')) {
    status = 'error';
  } else if (content.startsWith('tool result') || content.includes('completed') || content.includes('result')) {
    status = 'done';
  }

  return { name, status, args, argsPreview, rawContent: content };
}

function formatArgs(args: Record<string, unknown> | null): string {
  if (!args) return '';
  try {
    const cleaned: Record<string, unknown> = {};
    for (const key of Object.keys(args)) {
      if (key === '_comment' || key === '$comment' || key === '-comment') continue;
      cleaned[key] = args[key];
    }
    return JSON.stringify(cleaned, null, 2);
  } catch {
    return JSON.stringify(args, null, 2);
  }
}

function StatusIcon({ status }: { status: ToolInfo['status'] }) {
  switch (status) {
    case 'done':
      return <CircleCheck className="text-success flex-shrink-0" size={14} />;
    case 'error':
      return <CircleX className="text-danger flex-shrink-0" size={14} />;
    case 'running':
      return <Loader2 className="animate-spin text-accent flex-shrink-0" size={14} />;
  }
}

export function ToolGroup({ items }: ToolGroupProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set());
  const unread = useSessionStore((s) => s.getUnread());

  if (items.length === 0) return null;

  const tools = items.map((t) => parseTool(t.content));
  const hasUnread = items.some((t) => unread.has(t.content));

  const handleToolClick = (idx: number, tool: ToolInfo) => {
    // Open detail panel for this tool
    const detail = useDetailStore.getState();
    detail.openDetail({ type: 'tool', content: tool.rawContent, title: tool.name });

    // Keep existing expand/collapse behavior
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  return (
    <div className="border border-border-default rounded-lg bg-bg-secondary mb-3">
      {/* Group Header */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="tool-group-header flex items-center gap-2 px-3 py-2 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover/30 transition-colors w-full text-left select-none"
      >
        <Wrench size={14} />
        <span>{items.length} tools</span>
        {hasUnread && !isOpen && (
          <span className="w-2 h-2 rounded-full bg-accent flex-shrink-0" title="unread" />
        )}
        <span className="ml-auto text-text-tertiary">
          {isOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </button>

      {/* Tool rows */}
      {isOpen && (
        <>
          {tools.map((tool, i) => (
            <div key={i}>
              {i > 0 && <div className="border-t border-border-default" />}

              {/* Tool Row */}
              <div
                onClick={() => handleToolClick(i, tool)}
                className="msg tool flex items-center gap-2 min-h-[30px] px-3 cursor-pointer hover:bg-bg-hover/30 transition-colors select-none"
              >
                <StatusIcon status={tool.status} />
                <span className="text-xs font-mono text-text-primary truncate">{tool.name}</span>
                {tool.argsPreview && (
                  <span className="text-xs text-text-tertiary truncate">{tool.argsPreview}</span>
                )}
                <span className="ml-auto text-text-tertiary flex-shrink-0">
                  {expandedTools.has(i) ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </span>
              </div>

              {/* Expanded content */}
              {expandedTools.has(i) && (
                <div className="bg-bg-tertiary border-t border-border-default p-3 overflow-hidden">
                  <pre className="text-xs font-mono whitespace-pre-wrap leading-relaxed text-text-secondary">
                    {tool.args ? formatArgs(tool.args) : tool.rawContent}
                  </pre>
                </div>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
