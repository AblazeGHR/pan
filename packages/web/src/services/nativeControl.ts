export interface NativeControlState {
  workerId: string | null;
  workerGeneration?: number | null;
  mode: 'pan' | 'native' | 'switching';
  generation: number;
  error: string | null;
  enabled?: boolean;
  pendingEnabled?: boolean | null;
  passthrough?: boolean;
  passthroughPending?: boolean;
  settingsUncertain?: boolean;
}
export interface NativeConfiguration { enabled: boolean; pendingEnabled: boolean | null }
export interface NativeOutput extends NativeControlState { cursor: string; gap: boolean; data: string }

export async function nativeRequest<T>(sessionId: string, path = '', body?: object, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/native-control${path}`, {
    method: body ? 'POST' : 'GET', signal,
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  });
  if (!response.ok) {
    let code = '';
    try {
      const failure = await response.json() as { error?: { code?: unknown } };
      if (typeof failure.error?.code === 'string' && /^[a-z-]{1,64}$/.test(failure.error.code)) code = failure.error.code;
    } catch { /* malformed transport response: keep status only */ }
    throw new Error(`Native terminal request failed (${response.status}${code ? `: ${code}` : ''})`);
  }
  const envelope = await response.json() as { ok: boolean; result: T };
  if (!envelope.ok) throw new Error('Native terminal request rejected');
  return envelope.result;
}
