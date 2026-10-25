import { create } from 'zustand';
import type { AutomationData, AutomationSnapshot } from '@/types/automation';
import type { ActionRegistry } from '@/types/actions';
import { useActionRegistryStore } from './actionRegistryStore';

export class AutomationError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

// This module deliberately has no persistence middleware. Neither credentials
// nor keys enter the general API cache, URL, storage or diagnostic logging.
export const useAutomationAuth = create<{ token: string | null; authenticated: boolean; sessionDays: number; expiresAt: number; clear: () => void }>((set) => ({
  token: null, authenticated: false, sessionDays: 0, expiresAt: 0,
  clear: () => set({ token: null, authenticated: false, expiresAt: 0 }),
}));

export async function restoreAutomationSession() {
  const before = useAutomationAuth.getState();
  const response = await fetch('/api/automation/session', { credentials: 'same-origin' });
  if (!response.ok) {
    if (response.status === 401 && useAutomationAuth.getState() === before) before.clear();
    return;
  }
  const state = await response.json() as { authenticated: boolean; expiresAt: number | null; sessionDays: number };
  if (useAutomationAuth.getState() === before) useAutomationAuth.setState({
    authenticated: state.authenticated, expiresAt: (state.expiresAt ?? 0) * 1000,
    sessionDays: state.sessionDays, token: null,
  });
}
export async function automationRequest<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const auth = useAutomationAuth.getState();
  const protectedPath = path.startsWith('automation') || path.startsWith('jobs/action-jobs') || path.startsWith('jobs/job-templates') || path.startsWith('actions');
  const token = protectedPath ? useAutomationAuth.getState().token : null;
  const response = await fetch(`/api/${path}`, { method, credentials: 'same-origin', headers: {
    ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!response.ok) {
    if (response.status === 401 && useAutomationAuth.getState() === auth) useAutomationAuth.getState().clear();
    const data = await response.json().catch(() => ({})) as { detail?: unknown };
    throw new AutomationError(response.status, response.status === 401 ? 'User login required. Your draft is preserved.' :
      response.status === 409 ? 'Revision conflict. Your draft is preserved. Refresh, compare with the server, then reopen or explicitly rebase your draft.' :
      typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}
export async function automationLogin(key: string) {
  // Login must not attach a previous Bearer or expose a server-echoed key.
  const response = await fetch('/api/automation/login', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key }) });
  if (!response.ok) throw new Error('Login failed. Check your local automation/user.key.');
  const { token, expiresIn } = await response.json() as { token: string; expiresIn: number };
  useAutomationAuth.setState({ token, authenticated: true, expiresAt: Date.now() + expiresIn * 1000 });
}
export async function automationLogout() {
  try { await automationRequest('automation/logout', 'POST'); }
  finally { useAutomationAuth.getState().clear(); }
}

type State = { data: AutomationData | null; updatedAt: number; loading: boolean; error: string; refresh: (forceActions?: boolean) => Promise<void>; invalidate: () => Promise<void> };
let generation = 0;
let pending: Promise<void> | undefined;
export const useAutomationStore = create<State>((set, get) => ({
  data: null, updatedAt: 0, loading: false, error: '',
  refresh: (forceActions = false) => {
    if (pending) return pending;
    const version = generation;
    set({ loading: true, error: '' });
    const request = Promise.all([
      automationRequest<AutomationSnapshot>('automation'), useActionRegistryStore.getState().ensureRegistry({ force: forceActions }),
      automationRequest<{ sessions: AutomationData['sessions'] }>('sessions?summary=1'),
    ]).then(([automation, , sessions]) => {
      const actions = useActionRegistryStore.getState();
      if (!actions.hasLoaded) throw new Error(actions.error ?? 'Action registry unavailable');
      if (version === generation) set({ data: { automation, actions: actions.registry, sessions: sessions.sessions }, updatedAt: Date.now() });
    }).catch((error: unknown) => { if (version === generation) set({ error: error instanceof Error ? error.message : 'Refresh failed' }); })
      .finally(() => { if (pending === request) { pending = undefined; set({ loading: false }); } });
    pending = request;
    return request;
  },
  invalidate: () => { generation++; pending = undefined; return get().refresh(); },
}));

export async function automationMutation<T>(path: string, method: string, body?: unknown): Promise<T> {
  try { return await automationRequest<T>(path, method, body); }
  finally { await useAutomationStore.getState().invalidate(); }
}

export function createAutomationAction<T extends ActionRegistry>(item: unknown): Promise<T> {
  // Same serialized mutation/GET fencing as Actions. No redundant GET, catalog
  // invalidation or automation-ledger refresh after a registry-only mutation.
  return useActionRegistryStore.getState().runMutation((expectedRevision) =>
    automationRequest<T>('actions/actions', 'POST', { item, expectedRevision }));
}
