import { useRef, useEffect, useCallback } from 'react';
import { useDetailStore } from '@/stores/detailStore';
import { PanelHeader } from './PanelHeader';
import { MarkdownRenderer } from '@/components/chat/MarkdownRenderer';

export function DetailPanel() {
  const detailTarget = useDetailStore((s) => s.detailTarget);
  const panelWidth = useDetailStore((s) => s.panelWidth);
  const closeDetail = useDetailStore((s) => s.closeDetail);
  const setPanelWidth = useDetailStore((s) => s.setPanelWidth);
  const contentRef = useRef<HTMLDivElement>(null);

  const isResizing = useRef(false);

  // Auto-scroll to bottom when content changes
  useEffect(() => {
    if (detailTarget && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [detailTarget?.content]);

  // Resize handle handlers
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      isResizing.current = true;
      const startX = e.clientX;
      const startWidth = useDetailStore.getState().panelWidth;

      const handleMouseMove = (e: MouseEvent) => {
        if (!isResizing.current) return;
        const delta = startX - e.clientX;
        setPanelWidth(startWidth + delta);
      };

      const handleMouseUp = () => {
        isResizing.current = false;
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };

      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
    },
    [setPanelWidth],
  );

  if (!detailTarget) {
    return <aside className="w-0 overflow-hidden border-l-0" />;
  }

  const title = detailTarget.type === 'thinking' ? 'Thinking' : 'Tool';
  const subtitle = detailTarget.content
    ? detailTarget.content.replace(/\n/g, ' ').slice(0, 60)
    : '';

  return (
    <aside
      className="flex flex-col h-full border-l border-border-default bg-bg-secondary transition-[width] duration-[280ms] ease-in-out"
      style={{ width: panelWidth }}
    >
      {/* Resize handle on left edge */}
      <div
        className="absolute left-0 top-0 bottom-0 w-[4px] cursor-col-resize hover:bg-accent/50 z-10"
        style={{ marginLeft: -2 }}
        onMouseDown={handleMouseDown}
      />

      <PanelHeader title={title} subtitle={subtitle} onClose={closeDetail} />
      <div ref={contentRef} className="flex-1 overflow-y-auto p-4">
        {detailTarget.type === 'thinking' ? (
          <div className="text-sm text-text-secondary leading-relaxed">
            <MarkdownRenderer content={detailTarget.content} />
          </div>
        ) : (
          <pre className="text-sm text-text-secondary whitespace-pre-wrap leading-relaxed">
            {detailTarget.content}
          </pre>
        )}
      </div>
    </aside>
  );
}
