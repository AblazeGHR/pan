import { useEffect, useState } from 'react';
import { fetchTakeoverReadiness } from '@/services/api';
import type { Session } from '@/types';

const checking = { ready: false, reason: 'Checking CLI resume availability…' };

export function useTakeoverReadiness(session: Session | null | undefined) {
  const [value, setValue] = useState<{ key?: string; ready: boolean; reason: string | null }>(checking);
  const id = session?.id;
  const key = JSON.stringify([id, session?.cliSessionId, session?.historyTotal, session?.updatedAt, session?.workerStatus]);
  useEffect(() => {
    if (!id) return;
    let active = true;
    fetchTakeoverReadiness(id).then(result => {
      if (active) setValue({ key, ...result });
    }).catch((error: Error) => {
      if (active) setValue({ key, ready: false, reason: `Unable to verify CLI resume data: ${error.message}` });
    });
    return () => { active = false; };
  }, [id, key]);
  return value.key === key ? value : checking;
}
