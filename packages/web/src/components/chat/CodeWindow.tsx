import { memo, useMemo, useRef } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { diffLineClass } from './codePresentation';

const LINE_HEIGHT = 20;
const WINDOW_HEIGHT = 320;

/** Bound a single giant row as well as the outer message list. Copy uses the
 * original text in the caller, independent of which lines are mounted. */
export const CodeWindow = memo(function CodeWindow({ text, language }: { text: string; language: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const { lines, width } = useMemo(() => {
    const lines = text.split('\n');
    let width = 0;
    for (const line of lines) width = Math.max(width, line.length);
    return { lines, width };
  }, [text]);
  const height = Math.min(WINDOW_HEIGHT, lines.length * LINE_HEIGHT);
  const virtualizer = useVirtualizer({
    count: lines.length,
    getScrollElement: () => viewport.current,
    estimateSize: () => LINE_HEIGHT,
    initialRect: { width: 0, height },
    overscan: 5,
  });
  return (
    <div
      ref={viewport}
      role="region"
      tabIndex={0}
      aria-label={`${language} code`}
      data-testid="large-code-window"
      className="overflow-auto p-3 text-xs font-mono"
      style={{ maxHeight: WINDOW_HEIGHT + 24, height: height + 24 }}
    >
      <div style={{ position: 'relative', height: virtualizer.getTotalSize(), minWidth: '100%', width: `${width + 2}ch` }}>
        {virtualizer.getVirtualItems().map(row => (
          <div
            key={row.key}
            data-code-line={row.index}
            className={language === 'diff' ? diffLineClass(lines[row.index]!) : undefined}
            style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: LINE_HEIGHT,
              lineHeight: `${LINE_HEIGHT}px`, whiteSpace: 'pre', transform: `translateY(${row.start}px)` }}
          >
            {lines[row.index] || '\u00a0'}
          </div>
        ))}
      </div>
    </div>
  );
});
