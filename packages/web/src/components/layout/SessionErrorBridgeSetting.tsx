import { settingsText } from '@/components/layout/settingsCatalog';
import { useState } from 'react';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { ErrorNotificationExclusions } from './ErrorNotificationExclusions';

export function SessionErrorBridgeSetting() {
  const enabled = useAppSettingsStore((s) => s.sessionErrorBridge);
  const save = useAppSettingsStore((s) => s.saveSessionErrorBridge);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return <section data-testid="session-error-bridge-section" className="space-y-2">
    <h3 className="text-xs font-semibold text-text-primary" data-settings-id="session-error-bridge">{settingsText("session-error-bridge")}</h3>
    <label className="flex items-center gap-2 text-xs" data-settings-id="session-error-bridge-setting.1">
      <input type="checkbox" checked={enabled} disabled={busy} onChange={async (event) => {
        setBusy(true); setError('');
        try { await save(event.target.checked); }
        catch (reason) { setError(reason instanceof Error ? reason.message : 'Could not save Session error routing'); }
        finally { setBusy(false); }
      }} />{settingsText("session-error-bridge-setting.1")}</label>
    <p className="text-[11px] text-text-secondary" data-settings-id="session-error-bridge-setting.2">{settingsText("session-error-bridge-setting.2")}</p>
    <ErrorNotificationExclusions scope="behavior." />
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
  </section>;
}
