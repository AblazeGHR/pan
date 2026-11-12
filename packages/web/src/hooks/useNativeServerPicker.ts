import { useCallback, useEffect, useRef, useState } from 'react';
import type { NativePickerResult, NativePickerStatus } from '@/services/api';

/**
 * Shared request lifecycle for the Pan server's native dialog.
 *
 * Both consumers drive the same server picker singleton, so busy handling,
 * the 90-second desktop expectation, status copy and stale-result protection
 * live here instead of being duplicated per dialog kind. Only the dialog kind
 * (`mode`) and the copy differ.
 */
export type NativeServerPickerMode = 'files' | 'directories';

interface NativePickerCopy {
  pending: string;
  selected: string;
  cancelled: string;
  failed: string;
  timeout: string;
  busy: string;
  unavailable: string;
  requestFailed: string;
}

/** Attachment copy is shipped file-mode behavior; keep the wording verbatim. */
const FILE_COPY: NativePickerCopy = {
  pending: 'Opening a file dialog on the Pan server desktop (up to 90 seconds). Check that desktop or its Pan server attachments taskbar window; you can keep browsing below.',
  selected: 'Files selected. Confirm below to add these server attachments.',
  cancelled: 'Native file selection cancelled. You can browse below.',
  failed: 'Native file selection failed. Please use the file browser below.',
  timeout: 'Native file selection timed out. Please use the file browser below.',
  busy: 'A file dialog is already open on the server. Complete it or use the file browser below.',
  unavailable: 'No interactive Windows desktop is available on the server. Please use the file browser below.',
  requestFailed: 'Could not open the native file picker. The server may need an update. Please use the file browser below.',
};

const DIRECTORY_COPY: NativePickerCopy = {
  pending: 'Opening a folder dialog on the Pan server desktop (up to 90 seconds). Check that desktop or its Pan working directory picker taskbar window; you can keep browsing below.',
  selected: 'Folder selected on the Pan server desktop.',
  cancelled: 'Native folder selection cancelled. You can browse below.',
  failed: 'Native folder selection failed. Please use the directory browser below.',
  timeout: 'Native folder selection timed out. Please use the directory browser below.',
  busy: 'A dialog is already open on the server. Complete it or use the directory browser below.',
  unavailable: 'No interactive Windows desktop is available on the server. Please use the directory browser below.',
  requestFailed: 'Could not open the native folder picker. The server may need an update. Please use the directory browser below.',
};

export interface NativeServerPicker {
  busy: boolean;
  feedback: string;
  /** Run one dialog request; stale results are dropped, never applied. */
  pick: (
    request: () => Promise<NativePickerResult>,
    onSelected: (paths: string[]) => void,
  ) => Promise<void>;
  /** Drop the pending result and its feedback (manual edit, close, cancel). */
  reset: () => void;
}

/**
 * @param mode Dialog kind requested from the server.
 * @param resetKey Any identity change (e.g. the Session id) invalidates a
 *   result that was requested for the previous identity.
 */
export function useNativeServerPicker(
  mode: NativeServerPickerMode,
  resetKey?: unknown,
): NativeServerPicker {
  const identity = useRef(0);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState('');
  const copy = mode === 'directories' ? DIRECTORY_COPY : FILE_COPY;

  const reset = useCallback(() => {
    identity.current += 1;
    setFeedback('');
  }, []);

  // An unmounted dialog owner can never apply its result.
  useEffect(() => () => { identity.current += 1; }, []);

  // A different identity invalidates whatever the previous one requested.
  useEffect(() => { identity.current += 1; }, [resetKey]);

  const pick = useCallback(async (
    request: () => Promise<NativePickerResult>,
    onSelected: (paths: string[]) => void,
  ) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    const requestId = ++identity.current;
    setFeedback(copy.pending);
    try {
      const result = await request();
      if (requestId !== identity.current) return;
      if (result.status === 'selected') {
        setFeedback(copy.selected);
        onSelected(result.paths);
      } else {
        setFeedback(copy[result.status as Exclude<NativePickerStatus, 'selected'>]);
      }
    } catch {
      if (requestId === identity.current) setFeedback(copy.requestFailed);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }, [copy]);

  return { busy, feedback, pick, reset };
}
