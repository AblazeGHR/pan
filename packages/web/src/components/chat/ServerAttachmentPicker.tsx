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
    setFeedback('请在 Pan 服务端电脑选择文件；也可继续使用下方浏览器。');
    try {
      const result = await pickNativeServerFiles(sessionId, parseDirectoryInput(value).directory);
      if (requestId !== identity.current) return;
      if (result.status === 'selected') {
        setPaths(result.paths);
        setFeedback('文件已选择，确认后才会登记附件。');
      } else {
        setFeedback({
          cancelled: '已取消原生文件选择。',
          failed: '原生文件选择失败，请使用下方文件浏览器。',
          timeout: '原生文件选择已超时，请使用下方文件浏览器。',
          busy: '服务端已有文件选择窗口，请完成该窗口或使用下方文件浏览器。',
          unavailable: '服务端系统不支持或没有可用的交互桌面，请使用下方文件浏览器。',
        }[result.status]);
      }
    } catch {
      if (requestId === identity.current) setFeedback('无法调用原生文件选择器，请使用下方文件浏览器。');
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };
  return <div className="flex flex-col gap-3">
    <Button type="button" variant="secondary" disabled={busy} onClick={() => void pick()}>使用原生文件选择器</Button>
    {feedback && <p role="status" className="text-sm text-text-secondary">{feedback}</p>}
    {paths.length > 0 && <div>
      <ul>{paths.map((path) => <li key={path} className="break-all text-sm">{path}</li>)}</ul>
      <Button type="button" variant="primary" onClick={() => {
        const selected = paths;
        manual();
        selected.forEach((path) => onSelect(path));
      }}>确认添加所选附件</Button>
      <Button type="button" variant="secondary" onClick={manual}>取消选择</Button>
    </div>}
    <DirectoryInput value={value} onChange={(path) => { manual(); onChange(path); }}
      fileMode showRootsWhenEmpty onSelect={(path) => { manual(); onSelect(path); }} />
  </div>;
}
