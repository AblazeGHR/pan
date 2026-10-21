import { create } from 'zustand';
import { actionRequest } from '@/services/api';
import type { SessionOperationTemplate } from '@/types/actions';
let pending: Promise<void> | undefined;
export const useSessionOperationTemplateStore = create<{ data: SessionOperationTemplate | null; error: string; load: () => Promise<void> }>((set, get) => ({
  data: null, error: '', load: () => {
    if (get().data) return Promise.resolve();
    if (pending) return pending;
    pending = actionRequest<SessionOperationTemplate>('actions/session-operation-template').then((data) => set({ data, error: '' }))
      .catch((error: unknown) => set({ error: error instanceof Error ? error.message : 'Template unavailable' })).finally(() => { pending = undefined; });
    return pending;
  },
}));
