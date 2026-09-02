import { useState } from 'react';

/**
 * Fixed corner badge shown only in mock mode (?mock=1). Reminds the user the
 * demo runs without a backend and explains the drag affordances.
 */
export function DemoBadge() {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="fixed z-50 bottom-2 left-2 max-w-[calc(100vw-1rem)]">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="rounded-full border border-accent/60 bg-bg-secondary/95 px-2.5 py-1 text-[10px] font-medium text-accent shadow-panel"
      >
        MOCK DEMO · 无后端
      </button>
      {expanded && (
        <div className="mt-1 w-64 rounded border border-border-default bg-bg-secondary/95 p-2.5 text-[11px] leading-relaxed text-text-secondary shadow-panel">
          <p className="font-medium text-text-primary">Mock 交互说明</p>
          <ul className="mt-1 list-disc pl-4 space-y-0.5">
            <li>拖动卡片左侧 <span className="font-bold">::</span> 把手开始拖拽（原卡片保持原位）</li>
            <li>放到另一张卡片<span className="font-medium">中心</span> = mock「交给对方管理」</li>
            <li>放到卡片<span className="font-medium">边缘 / 两卡之间</span> = 插入排序，并切换为自定义排序</li>
            <li>点击工具栏 Sort 按钮循环 recent → name → custom</li>
          </ul>
        </div>
      )}
    </div>
  );
}
