import { useCallback, useEffect, useRef, useState } from 'react';
import { automationRequest, useAutomationAuth } from './automationStore';
import { canonicalError, canonicalJobs } from '@/services/canonicalJobs';
import type { CanonicalKind, CanonicalObject } from '@/types/canonicalJobs';
import type { CreatorReferences, EventCreator, SchedulerProvider } from '@/types/automationJobs';
import type { AutomationSnapshot } from '@/types/automation';

export async function canonicalCollection(kind: CanonicalKind): Promise<CanonicalObject[]> {
  const items: CanonicalObject[] = [], seen = new Set<string>();
  let cursor: string | null = null;
  do {
    const page = await canonicalJobs.list(kind, cursor);
    items.push(...page.items);
    cursor = page.nextCursor;
    if (cursor && seen.has(cursor)) throw new Error('Repeated canonical page cursor');
    if (cursor) seen.add(cursor);
  } while (cursor);
  return items;
}

export function useAutomationWorkspace() {
  const authorization = useAutomationAuth((s) => s.token || s.authenticated);
  const [snapshot, setSnapshot] = useState<{ jobs: CanonicalObject[]; triggers: CanonicalObject[]; sources: EventCreator[]; references: Record<string, CreatorReferences>; providers: SchedulerProvider[] }>({ jobs: [], triggers: [], sources: [], references: {}, providers: [] });
  const [error, setError] = useState(''), [loading, setLoading] = useState(false);
  const [observations, setObservations] = useState<Pick<AutomationSnapshot, 'definitions' | 'diagnostics'>>({ definitions: [], diagnostics: [] });
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    const version = ++generation.current;
    if (!authorization) { setSnapshot({ jobs: [], triggers: [], sources: [], references: {}, providers: [] }); setObservations({ definitions: [], diagnostics: [] }); setLoading(false); return; }
    setLoading(true); setError('');
    try {
      const [jobs, creators, triggers, providers] = await Promise.all([
        canonicalCollection('jobs'), canonicalCollection('event-creators'), canonicalCollection('triggers'), canonicalJobs.creatorProviders(),
      ]);
      const sources = creators.filter((s) => {
        const provider = s.providerRef as { type?: string; id?: string } | undefined;
        return s.state !== 'retired' && provider?.type === 'time' && provider.id === 'scheduler';
      }) as EventCreator[];
      const references = Object.fromEntries(await Promise.all(sources.map(async (s) => [s.id, await canonicalJobs.creatorReferences(s.id)] as const)));
      if (version === generation.current) setSnapshot({ jobs, triggers, sources, references, providers: providers.items });
      const observed = await automationRequest<AutomationSnapshot>('automation');
      if (version === generation.current) setObservations({ definitions: observed.definitions ?? [], diagnostics: observed.diagnostics ?? [] });
    } catch (e) { if (version === generation.current) setError(canonicalError(e)); }
    finally { if (version === generation.current) setLoading(false); }
  }, [authorization]);
  useEffect(() => { void refresh(); return () => { generation.current++; }; }, [refresh]);
  return { ...snapshot, ...observations, error, loading, refresh };
}
