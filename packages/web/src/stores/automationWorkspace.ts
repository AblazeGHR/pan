import { useCallback, useEffect, useRef, useState } from 'react';
import { automationRequest, useAutomationAuth } from './automationStore';
import type { ActionJob, JobEdit, SchedulerSource } from '@/types/automationJobs';

export function useAutomationWorkspace() {
  const token = useAutomationAuth((s) => s.token);
  const [snapshot, setSnapshot] = useState<{ jobs: ActionJob[]; sources: SchedulerSource[]; operations: JobEdit[]; schema: unknown }>({ jobs: [], sources: [], operations: [], schema: {} });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    const version = ++generation.current;
    if (!token) { setLoading(false); return; }
    setLoading(true); setError('');
    try {
      const [jobs, sources, edits] = await Promise.all([
        automationRequest<{ jobs: ActionJob[] }>('jobs/action-jobs'),
        automationRequest<{ sources: SchedulerSource[]; payloadSchema: unknown }>('scheduler/event-sources'),
        automationRequest<{ operations: JobEdit[] }>('automation/job-edits'),
      ]);
      if (version === generation.current) setSnapshot({ jobs: jobs.jobs ?? [], sources: sources.sources ?? [], operations: edits.operations ?? [], schema: sources.payloadSchema });
    } catch (e) { if (version === generation.current) setError(e instanceof Error ? e.message : 'Workspace refresh failed'); }
    finally { if (version === generation.current) setLoading(false); }
  }, [token]);
  useEffect(() => { void refresh(); return () => { generation.current++; }; }, [refresh]);
  return { ...snapshot, error, loading, refresh };
}
