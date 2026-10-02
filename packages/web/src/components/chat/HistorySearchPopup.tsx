import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** Keep popup geometry independent of the narrow shared tools sidebar. */
export function HistorySearchPopup({ container, children }: { container?: HTMLElement | null; children: ReactNode }) {
  return container ? createPortal(children, container) : children;
}
