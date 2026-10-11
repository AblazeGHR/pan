import { useEffect, useState } from 'react';
import { useNativeControlStore } from '@/stores/nativeControlStore';

/** Session-scoped policy, exposed on Adapter > Codex (not a global default). */
export function CodexNativePassthrough({ sessionId }: { sessionId: string | null }) {
  const state = useNativeControlStore((s) => (sessionId ? s.sessions[sessionId] : undefined));
  const busy = useNativeControlStore((s) => (sessionId ? s.switching[sessionId] : false));
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    setError('');
    if (sessionId) {
      void useNativeControlStore
        .getState()
        .refresh(sessionId)
        .then(() => {
          if (live) setLoadedId(sessionId);
        })
        .catch(() => {
          if (live) setError('无法读取原生操作设置，请重新打开后重试。');
        });
    }
    return () => {
      live = false;
    };
  }, [sessionId]);
  return (
    <div className="px-3 py-2">
      <label className="flex items-center gap-2 text-xs text-text-primary">
        <input
          type="checkbox"
          aria-label="允许所有 Codex 原生操作"
          checked={state?.passthrough === true}
          disabled={!sessionId || loadedId !== sessionId || busy}
          onChange={(event) => {
            if (!sessionId) return;
            setError('');
            void useNativeControlStore
              .getState()
              .configurePassthrough(sessionId, event.target.checked)
              .catch(() => setError('原生操作设置未确认，请重试；不会自动重启 Worker。'));
          }}
        />
        允许所有 Codex 原生操作
      </label>
      <p className="mt-1 text-[11px] leading-4 text-text-tertiary">
        {sessionId ? '仅当前 Codex Session。' : '请先选择一个 Codex Session。'}
        默认拦截；开启后原生操作可修改 Codex 全局配置，Pan 设置可能与事实不符。 Pan
        只同步已知事件，不推测未知操作；成功重启 Worker 后取消不一致标记。
      </p>
      {state?.settingsUncertain && (
        <p role="status" className="mt-1 text-[11px] text-warning">
          Pan Session 设置可能与事实不符（成功重启 Worker 后重新确认）。
        </p>
      )}
      {state?.passthroughPending && (
        <p role="status" className="mt-1 text-[11px] text-warning">
          设置已保存，Worker 尚未确认；请重试。
        </p>
      )}
      {error && (
        <p role="alert" className="mt-1 text-[11px] text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
