import { memo, useLayoutEffect, useRef, useState } from 'react';
import { useSessionStore } from '@/stores/sessionStore';
import { jobBadgeFits, measureInfoArea, JOB_METRIC_ATTR } from './sessionInfoAreaFit';

export type SessionJobIndicator = 'running' | 'scheduled';

interface SessionInfoAreaProps {
  sessionId: string;
  /** target 为本 Session 的 Job 聚合值；null 表示无 J 标识。 */
  indicator: SessionJobIndicator | null;
  /** 队列数徽标：按 queue_pending 公开投影的待处理数。 */
  queuePendingCount: number;
  queueAllLocked: boolean;
  /** 未读 done 徽标：0 时完全不渲染；ack 边界用 generation。 */
  unreadDoneCount: number;
  unreadDoneGeneration: number;
  adapter?: string;
}

/**
 * Session 卡片右下角信息区（底行）：队列数徽标、未读 done 徽标、adapter 标签，
 * 以及 target 为本 Session 的 Job 聚合标识 J。
 *
 * J 的显隐按信息区实际可用宽度决定（sessionInfoAreaFit.ts）：与兄弟标识放不下
 * 时整枚卸载——无残胶囊、无预留 gap，数字徽标与 adapter 保持完整，底行右缘
 * （right 锚定）始终不动。不按 viewport 禁用：宽松桌面与宽松移动都会显示。
 * 重测依赖只有会改变兄弟宽度的输入（队列数/未读数/adapter）加一次性
 * document.fonts.ready；rail 固定 72px，不需要 ResizeObserver/timer/轮询。
 * 首次渲染默认可见，useLayoutEffect 在绘制前完成测量并按需隐藏，不产生闪烁。
 *
 * J 的自然宽度由常驻探针（与徽标同类、绝对定位 + visibility:hidden）提供：
 * 徽标隐藏（卸载）后探针仍在，fonts.ready 重测因此在隐藏态也能读到新字形的
 * J 宽度——无缓存陈旧，两个方向（字体变窄应显示 / 变宽不得显示）都正确。
 *
 * 测量在本组件内进行：行元素是本组件的根节点，其 ref 在自身 layout effect
 * 之前附着（子组件 effect 里读父级 ref 会得到 null）。
 */
export const SessionInfoArea = memo(function SessionInfoArea({
  sessionId,
  indicator,
  queuePendingCount,
  queueAllLocked,
  unreadDoneCount,
  unreadDoneGeneration,
  adapter,
}: SessionInfoAreaProps) {
  const rowRef = useRef<HTMLSpanElement>(null);
  const probeRef = useRef<HTMLSpanElement>(null);
  const [jobBadgeFitsRow, setJobBadgeFitsRow] = useState(true);

  useLayoutEffect(() => {
    if (!indicator) return;
    const row = rowRef.current;
    if (!row) return;
    let cancelled = false;
    const measure = () => {
      setJobBadgeFitsRow(jobBadgeFits(measureInfoArea(row, probeRef.current)));
    };
    measure();
    // Webfont swap (Inter, display:swap) measurably changes the badge glyph
    // widths after first paint (probe: J 12.22 -> 13.47px, adapter +2.2px),
    // which can flip a boundary case decided on fallback metrics. One-shot
    // re-measure once fonts settle — not a poll: document.fonts.ready resolves
    // once. The probe keeps the J width fresh even while the badge is hidden.
    const fonts = document.fonts;
    if (fonts && fonts.status !== 'loaded') {
      fonts.ready.then(() => {
        if (!cancelled) measure();
      }).catch(() => {});
    }
    return () => { cancelled = true; };
  }, [indicator, queuePendingCount, unreadDoneCount, adapter]);

  return (
    <span className="session-quick-action-adapter-row" ref={rowRef}>
      {indicator && (
        // J 自然宽度探针：与徽标完全同类（同字体度量），绝对定位 +
        // visibility:hidden（CSS）不占布局、不绘制、不进 a11y 树。徽标隐藏
        // （卸载）时它仍在，是隐藏态字体重测的唯一 J 宽度来源。
        <span
          ref={probeRef}
          className="session-job-badge"
          {...{ [JOB_METRIC_ATTR]: 'probe' }}
          aria-hidden="true"
        >
          J
        </span>
      )}
      {indicator && jobBadgeFitsRow && (
        // J 标识（底行最左，DOM 首项）：target 为本 Session 的 Job 聚合。
        // 只读：事件全部拦截，不清零、不导航、不触发卡片选中；颜色由
        // data-job-state 驱动（scheduled 黄 / running 蓝），几何与队列/
        // 未读徽标一致；放不下时整枚卸载（宽度驱动，非 viewport）。
        <span
          className="session-job-badge"
          {...{ [JOB_METRIC_ATTR]: 'badge' }}
          data-testid="session-job-badge"
          data-job-state={indicator}
          role="img"
          aria-label={indicator === 'running'
            ? 'A background Job targeting this Session is running'
            : 'A scheduled Job targeting this Session is waiting for its next fire'}
          title={indicator === 'running'
            ? '后台 Job 运行中（target = 本 Session）'
            : '定时 Job 待调度（target = 本 Session）'}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          onDoubleClick={(e) => e.stopPropagation()}
        >
          J
        </span>
      )}
      {queuePendingCount > 0 && (
        // 队列数徽标（done 徽标左侧，独立于 done 计数）。只读：单击 /
        // 双击只拦截卡片事件，不清零、不删消息、不隐藏计数，数值只随
        // 真实队列变化减少。非空且所有待处理项都带真实锁（手动锁 /
        // 自动 report 锁）时用 danger 色系，否则用 accent 色系；与可双击
        // 清零的 done 徽标以色系、光标（default）与文案区分。
        <span
          className={`session-queue-badge${queueAllLocked ? ' session-queue-badge-locked' : ''}`}
          data-testid="session-queue-badge"
          data-queue-all-locked={queueAllLocked ? 'true' : 'false'}
          role="img"
          aria-label={`${queuePendingCount} queued pending message(s)${queueAllLocked ? ', all locked' : ''}. Read-only; it changes only with the real queue.`}
          title={`Queue: ${queuePendingCount} pending${queueAllLocked ? ' · all locked' : ''}（只读，只随真实队列变化）`}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          onDoubleClick={(e) => e.stopPropagation()}
        >
          {queuePendingCount > 99 ? '99+' : queuePendingCount}
        </span>
      )}
      {unreadDoneCount > 0 && (
        // 未读 done 徽标（adapter 标签左侧）。单击只拦截冒泡、不选中
        // 卡片也不清零：否则第一次 click 会经“选择即读”把徽标清零并
        // 卸载，第二次 click 失去目标，双击永远无法成立。清零只在
        // 双击（或选择会话）时发生；事件拦截同时防止卡片选中。
        <span
          className="session-unread-done-badge"
          data-testid="session-unread-done-badge"
          role="img"
          aria-label={`${unreadDoneCount} unread done. Double-click to mark as read.`}
          title={`${unreadDoneCount} unread done · 双击标记已读`}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          onDoubleClick={(e) => {
            e.stopPropagation();
            if (unreadDoneGeneration <= 0) return;
            void useSessionStore.getState()
              .ackSessionUnread(sessionId, unreadDoneGeneration);
          }}
        >
          {unreadDoneCount > 99 ? '99+' : unreadDoneCount}
        </span>
      )}
      {adapter && (
        <span
          className="session-quick-action-adapter"
          data-testid="session-adapter-badge"
          role="img"
          aria-label={`Adapter ${adapter}`}
          title={`Adapter: ${adapter}`}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
        >
          {adapter}
        </span>
      )}
    </span>
  );
});
