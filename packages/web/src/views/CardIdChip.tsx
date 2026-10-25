import { copyText } from '@/utils/clipboard';
import { useUIStore } from '@/stores/uiStore';

/**
 * Readable Card id at the bottom of a Locker card. Small monospace text with a
 * single-line ellipsis, so a 69-character id never widens or overflows the card
 * on 390/320. The full id stays available on hover (`title`) and in the
 * accessible name; clicking copies it and reports the result as a toast.
 */
export function CardIdChip({
  id,
  testId,
  kind,
}: {
  id: string;
  testId: string;
  kind: 'interaction' | 'reminding';
}) {
  return (
    <div className="locker-card-id-row">
      <button
        type="button"
        className="locker-card-id"
        data-testid={testId}
        data-card-id-value={id}
        title={`${id} · 点击复制`}
        aria-label={`复制${kind === 'interaction' ? '交互' : '提醒'}卡片 ID：${id}`}
        onClick={() => {
          void copyText(id)
            .then(() => useUIStore.getState().showToast('卡片 ID 已复制', 'info'))
            .catch(() => useUIStore.getState().showToast('复制失败：剪贴板不可用', 'error'));
        }}
      >
        <span className="locker-card-id-label" aria-hidden="true">
          ID
        </span>
        <span className="locker-card-id-text">{id}</span>
      </button>
    </div>
  );
}
