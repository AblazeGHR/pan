import { createContext, useContext, useEffect, useRef, type ReactNode } from 'react';
import { useBlocker } from 'react-router-dom';

type DraftState = { dirty: boolean; busy: boolean };
const Drafts = createContext<Map<object, { current: DraftState }> | null>(null);

/** One Router blocker for all mounted tab drafts (Router supports only one). */
export function DraftBoundary({ children }: { children: ReactNode }) {
  const drafts = useRef(new Map<object, { current: DraftState }>());
  const dirty = () => [...drafts.current.values()].some((d) => d.current.dirty || d.current.busy);
  const busy = () => [...drafts.current.values()].some((d) => d.current.busy);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => currentLocation.pathname !== nextLocation.pathname && dirty());
  useEffect(() => {
    if (blocker.state === 'blocked') {
      if (!busy() && window.confirm('Leave unsaved automation changes? Incomplete setup remains disabled and can be resumed.')) blocker.proceed();
      else blocker.reset();
    }
  }, [blocker]);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', unload);
    return () => window.removeEventListener('beforeunload', unload);
  }, []);
  return <Drafts.Provider value={drafts.current}>{children}</Drafts.Provider>;
}

export function useAutomationDraft(dirty: boolean, busy: boolean) {
  const registry = useContext(Drafts);
  const key = useRef({});
  const state = useRef({ dirty, busy }); state.current = { dirty, busy };
  useEffect(() => { registry?.set(key.current, state); const identity = key.current; return () => { registry?.delete(identity); }; }, [registry]);
}
