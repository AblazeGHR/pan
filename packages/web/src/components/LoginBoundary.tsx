import { useEffect, useState, type ReactNode } from 'react';
import { automationLogin, restoreAutomationSession, useAutomationAuth } from '@/stores/automationStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';

/** The normal Pan entry renders no business tree before session restoration. */
export function LoginBoundary({ children }: { children: ReactNode }) {
  const authenticated = useAutomationAuth((state) => state.authenticated);
  const [ready, setReady] = useState(false);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const restore = () => void restoreAutomationSession().catch(() => {
      if (active) setError('无法连接 Pan，请稍后重试。');
    }).finally(() => { if (active) setReady(true); });
    restore();
    window.addEventListener('focus', restore);
    return () => { active = false; window.removeEventListener('focus', restore); };
  }, []);
  useEffect(() => {
    if (authenticated) void useAppSettingsStore.getState().loadSettings();
  }, [authenticated]);
  useEffect(() => {
    if (!authenticated) return;
    let pending = false;
    // User activity also renews browser Cookies during a WS-only workflow.
    // No timer, heartbeat renewal or passive-tab lifetime extension.
    const activity = () => {
      const state = useAutomationAuth.getState();
      if (pending || !state.sessionDays || state.expiresAt - Date.now() > state.sessionDays * 86400000 / 2) return;
      pending = true;
      void restoreAutomationSession().catch(() => {}).finally(() => { pending = false; });
    };
    window.addEventListener('pointerdown', activity, { passive: true });
    window.addEventListener('keydown', activity);
    return () => { window.removeEventListener('pointerdown', activity); window.removeEventListener('keydown', activity); };
  }, [authenticated]);
  if (!ready) return <main className="p-6" role="status">正在确认登录状态…</main>;
  if (authenticated) return children;
  return <main className="flex min-h-screen items-center justify-center bg-bg-primary p-4 text-text-primary">
    <section className="w-full max-w-[24rem] space-y-4 rounded-lg border border-border-default p-6" aria-label="Pan login">
      <h1 className="text-2xl font-semibold">登录 Pan</h1>
      <p className="text-sm text-text-secondary">使用管理员密码登录。登录期限沿用此 Pan 的授权设置。</p>
      <form className="space-y-4" onSubmit={(event) => {
        event.preventDefault(); const secret = password; setPassword(''); setBusy(true); setError('');
        void automationLogin(secret).catch((reason: unknown) => {
          setError(reason instanceof Error ? reason.message : '登录失败');
        }).finally(() => setBusy(false));
      }}>
        <label className="block space-y-2 text-sm"><span>管理员密码</span>
          <input className="w-full rounded border border-border-default bg-transparent p-2" name="password"
            type="password" autoComplete="current-password" required disabled={busy}
            value={password} onChange={(event) => setPassword(event.target.value)} />
        </label>
        <button className="w-full rounded bg-accent p-2 text-white disabled:opacity-50" type="submit" disabled={busy}>
          {busy ? '正在登录…' : '登录'}
        </button>
      </form>
      {error && <p className="text-sm text-danger" role="alert">{error}</p>}
    </section>
  </main>;
}
