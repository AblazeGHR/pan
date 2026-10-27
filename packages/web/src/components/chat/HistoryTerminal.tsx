import { CircleAlert, Check } from 'lucide-react';
import type { HistoryTerminal as Terminal } from '@/types';

/** Pan execution metadata, independent from provider prose. */
export function HistoryTerminal({ terminal }: { terminal: Terminal }) {
  if (terminal.status === 'done') return (
    <div data-terminal-id={terminal.eventId} data-terminal-status="done"
      className="flex items-center gap-1 px-3 sm:px-6 lg:px-8 py-1 text-xs text-text-tertiary">
      <Check size={12} aria-hidden="true" /><span>已完成</span>
    </div>
  );
  const error = terminal.error;
  return (
    <section data-terminal-id={terminal.eventId} data-terminal-status="error"
      className="mx-3 sm:mx-6 lg:mx-8 my-2 border-l-2 border-red-400 pl-3 py-2 text-sm">
      <div className="flex items-center gap-2 text-red-400">
        <CircleAlert size={16} aria-hidden="true" /><strong>执行出错</strong>
        <time className="ml-auto text-xs text-text-tertiary" dateTime={terminal.occurredAt}>
          {new Date(terminal.occurredAt).toLocaleString()}
        </time>
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words text-text-secondary">{error?.message?.split('\n')[0] || '未提供错误详情'}</p>
      <details className="mt-1 text-xs text-text-tertiary">
        <summary className="cursor-pointer">诊断详情</summary>
        <dl><dt>来源</dt><dd>{error?.source || '未提供'}</dd>
          <dt>代码</dt><dd>{error?.code ?? '未提供'}</dd></dl>
        <pre className="whitespace-pre-wrap break-words">{error?.message}</pre>
        {error?.truncated && <p>诊断内容已截断</p>}
      </details>
    </section>
  );
}
