import { useEffect, useRef, useState } from 'react';
import { DirectoryInput } from '@/components/session/DirectoryInput';
import { Button } from '@/components/ui/Button';
import { pickNativeServerFiles } from '@/services/api';
import { parseDirectoryInput } from '@/utils/directoryInput';

interface Props {
  sessionId: string;
  value: string;
  onChange: (path: string) => void;
  onSelect: (path: string) => void;
}

export function ServerAttachmentPicker({ sessionId, value, onChange, onSelect }: Props) {
  const identity = useRef(0);
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [paths, setPaths] = useState<string[]>([]);
  const [feedback, setFeedback] = useState('');
  useEffect(() => () => { identity.current += 1; }, [sessionId]);
  const manual = () => {
    identity.current += 1;
    setPaths([]);
    setFeedback('');
  };
  const pick = async () => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setPaths([]);
    const requestId = ++identity.current;
    setFeedback('Opening a file dialog on the Pan server desktop (up to 90 seconds). Check that desktop or its Pan server attachments taskbar window; you can keep browsing below.');
    try {
      const result = await pickNativeServerFiles(sessionId, parseDirectoryInput(value).directory);
      if (requestId !== identity.current) return;
      if (result.status === 'selected') {
        setPaths(result.paths);
        setFeedback('Files selected. Confirm below to add these server attachments.');
      } else {
        setFeedback({
          cancelled: 'Native file selection cancelled. You can browse below.',
          failed: 'Native file selection failed. Please use the file browser below.',
          timeout: 'Native file selection timed out. Please use the file browser below.',
          busy: 'A file dialog is already open on the server. Complete it or use the file browser below.',
          unavailable: 'No interactive Windows desktop is available on the server. Please use the file browser below.',
        }[result.status]);
      }
    } catch {
      if (requestId === identity.current) setFeedback('Could not open the native file picker. The server may need an update. Please use the file browser below.');
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  return <div className="flex flex-col gap-3">
    <Button type="button" variant="secondary" disabled={busy} onClick={() => void pick()}>Open server file picker</Button>
    {feedback && <p role="status" className="text-sm text-text-secondary">{feedback}</p>}
    {paths.length > 0 && <div>
      <ul>{paths.map((path) => <li key={path} className="break-all text-sm">{path}</li>)}</ul>
      <Button type="button" variant="primary" onClick={() => {
        const selected = paths;
        manual();
        selected.forEach((path) => onSelect(path));
      }}>Confirm selected attachments</Button>
      <Button type="button" variant="secondary" onClick={manual}>Cancel selection</Button>
    </div>}
    <DirectoryInput value={value} onChange={(path) => { manual(); onChange(path); }}
      fileMode showRootsWhenEmpty onSelect={(path) => { manual(); onSelect(path); }} />
  </div>;
}
