import { useState } from 'react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { ErrorNotificationExclusions } from './ErrorNotificationExclusions';

export function SessionErrorBridgeSetting() {
  const enabled = useAppSettingsStore((s) => s.sessionErrorBridge);
  const save = useAppSettingsStore((s) => s.saveSessionErrorBridge);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <section data-testid="session-error-bridge-section" className="space-y-2">
    <h3 className="text-xs font-semibold text-text-primary">Session errors → msgBridge</h3>
    <label className="flex items-center gap-2 text-xs">
      <input type="checkbox" checked={enabled} disabled={busy} onChange={async (event) => {
        setBusy(true); setError('');
        try { await save(event.target.checked); }
        catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save Session error routing'); }
        finally { setBusy(false); }
      }} />Send Session errors to the Session’s msgBridge
    </label>
    <p className="text-[11px] text-text-secondary">Uses each Session’s System, Browser and QQ Report connections. Global error notifications in Notifications work independently. Matching channels and targets receive one notification per error.</p>
    <ErrorNotificationExclusions />
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
  </section>;
}
