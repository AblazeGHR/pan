import { useState } from 'react';
import { DirectoryInput } from '@/components/session/DirectoryInput';
import { Button } from '@/components/ui/Button';
import { pickNativeServerFiles } from '@/services/api';
import { parseDirectoryInput } from '@/utils/directoryInput';
import { useNativeServerPicker } from '@/hooks/useNativeServerPicker';

interface Props {
  sessionId: string;
  value: string;
  onChange: (path: string) => void;
  onSelect: (path: string) => void;
}

export function ServerAttachmentPicker({ sessionId, value, onChange, onSelect }: Props) {
  const { busy, feedback, pick, reset } = useNativeServerPicker('files', sessionId);
  const [paths, setPaths] = useState<string[]>([]);
  // The native dialog is shared with New Session; this component keeps the
  // attachment-specific staging step (explicit confirmation) on top of it.
  const manual = () => {
    reset();
    setPaths([]);
  };
  const pickFiles = async () => {
    setPaths([]);
    await pick(
      () => pickNativeServerFiles(sessionId, parseDirectoryInput(value).directory),
      (selected) => setPaths(selected),
    );
  };
  return <div className="flex flex-col gap-3">
    <Button type="button" variant="secondary" disabled={busy} onClick={() => void pickFiles()}>Open server file picker</Button>
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
