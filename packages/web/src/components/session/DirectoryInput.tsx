import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronUp, File as FileIcon, Folder, Loader2 } from 'lucide-react';
import { fetchDirectories, type DirectoryListResponse } from '@/services/api';
import { Button } from '@/components/ui/Button';
import { parseDirectoryInput } from '@/utils/directoryInput';

export interface DirectoryInputStatus {
  directory: string;
  valid: boolean;
  loading: boolean;
  error: string | null;
}

interface DirectoryInputProps {
  value: string;
  onChange: (value: string) => void;
  onSelect?: (path: string) => void;
  fileMode?: boolean;
  /** Directory inputs select directories; attachment inputs select files. */
  selectDirectories?: boolean;
  /** The attachment dialog shows roots immediately; New Session waits for input. */
  showRootsWhenEmpty?: boolean;
  onStatusChange?: (status: DirectoryInputStatus) => void;
  inputTestId?: string;
}

function appendSeparator(path: string): string {
  if (path.endsWith('\\') || path.endsWith('/')) return path;
  return `${path}\\`;
}

function invalidDirectoryMessage(error: unknown): string {
  if (error instanceof Error && /HTTP 403\b/.test(error.message)) {
    return '无权读取当前目录';
  }
  return '当前目录非法';
}

/**
 * One shared path input and one shared result list for attachments and New
 * Session. The text after the final separator is only a local filter over a
 * server listing; the server still validates the base directory.
 */
export function DirectoryInput({
  value,
  onChange,
  onSelect,
  fileMode = false,
  selectDirectories = false,
  showRootsWhenEmpty = false,
  onStatusChange,
  inputTestId = 'directory-input',
}: DirectoryInputProps) {
  const requestIdRef = useRef(0);
  const directoryClickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [data, setData] = useState<DirectoryListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const parts = useMemo(() => parseDirectoryInput(value), [value]);
  const hasInput = parts.input.length > 0;

  useEffect(() => () => {
    if (directoryClickTimerRef.current) clearTimeout(directoryClickTimerRef.current);
  }, []);

  useEffect(() => {
    const requestId = ++requestIdRef.current;
    if (!hasInput && !showRootsWhenEmpty) {
      setData(null);
      setError(null);
      setLoading(false);
      onStatusChange?.({ directory: '', valid: false, loading: false, error: null });
      return;
    }

    setLoading(true);
    setError(null);
    onStatusChange?.({ directory: parts.directory, valid: false, loading: true, error: null });
    fetchDirectories(parts.directory || undefined, fileMode)
      .then((result) => {
        if (requestId !== requestIdRef.current) return;
        setData(result);
        setError(null);
        onStatusChange?.({ directory: parts.directory, valid: true, loading: false, error: null });
      })
      .catch((reason: unknown) => {
        if (requestId !== requestIdRef.current) return;
        setData(null);
        const message = invalidDirectoryMessage(reason);
        setError(message);
        onStatusChange?.({ directory: parts.directory, valid: false, loading: false, error: message });
      })
      .finally(() => {
        if (requestId === requestIdRef.current) setLoading(false);
      });

    return () => {
      if (requestId === requestIdRef.current) requestIdRef.current += 1;
    };
  }, [fileMode, hasInput, onStatusChange, parts.directory, showRootsWhenEmpty]);

  const normalizedSearch = parts.search.toLocaleLowerCase();
  const visibleEntries = data?.entries.filter((entry) => {
    if (!normalizedSearch) return true;
    return `${entry.name} ${entry.path}`.toLocaleLowerCase().includes(normalizedSearch);
  }) ?? [];

  const chooseDirectory = (path: string) => {
    if (selectDirectories) {
      onSelect?.(path);
      return;
    }
    onChange(appendSeparator(path));
  };

  const handleDirectoryClick = (path: string) => {
    // A directory click means "select" in New Session, but it may be the
    // first click of a double-click used to enter the directory. Wait for
    // the double-click event so the first click cannot finish the flow.
    if (directoryClickTimerRef.current) clearTimeout(directoryClickTimerRef.current);
    directoryClickTimerRef.current = setTimeout(() => {
      directoryClickTimerRef.current = null;
      chooseDirectory(path);
    }, 250);
  };

  const handleDirectoryDoubleClick = (path: string) => {
    if (directoryClickTimerRef.current) {
      clearTimeout(directoryClickTimerRef.current);
      directoryClickTimerRef.current = null;
    }
    // Double-clicking always enters a directory, including when a consumer's
    // single-click semantics are to select it (New Session).
    onChange(appendSeparator(path));
  };

  return (
    <div className="flex flex-col gap-3" data-testid="directory-input-panel">
      <label className="flex flex-col gap-1 text-xs text-text-secondary">
        <span>目录路径</span>
        <input
          type="text"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="D:\\project\\app\\文件名"
          aria-label="目录路径"
          data-testid={inputTestId}
          className="rounded border border-border-muted bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none placeholder:text-text-tertiary focus:border-accent"
        />
      </label>
      {error && <p className="text-sm text-danger" data-testid="directory-error">{error}</p>}
      {!error && parts.search && data && (
        <p className="text-xs text-text-tertiary">
          在 {data.current || parts.directory || '服务器根位置'} 中检索“{parts.search}”
        </p>
      )}
      {loading && <div className="flex items-center gap-2 text-sm text-text-secondary"><Loader2 size={15} className="animate-spin" />加载中…</div>}
      {!loading && !error && data && data.entries.length > 0 && visibleEntries.length === 0 && (
        <div className="p-3 text-sm text-text-tertiary">没有匹配的条目</div>
      )}
      {!loading && !error && data && data.entries.length === 0 && (
        <div className="p-3 text-sm text-text-tertiary">空目录</div>
      )}
      {!error && data && visibleEntries.length > 0 && (
        <div data-testid="directory-entries" className="dir-scroll max-h-64 overflow-y-auto rounded border border-border-muted bg-bg-primary">
          {visibleEntries.map((entry) => (
            <button
              key={entry.path}
              type="button"
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-tertiary"
              onClick={() => entry.isDirectory ? handleDirectoryClick(entry.path) : onSelect?.(entry.path)}
              onDoubleClick={() => entry.isDirectory && handleDirectoryDoubleClick(entry.path)}
            >
              {entry.isDirectory ? <Folder size={15} className="shrink-0 text-text-tertiary" /> : <FileIcon size={15} className="shrink-0 text-text-tertiary" />}
              <span className="truncate">{entry.name}</span>
            </button>
          ))}
        </div>
      )}
      {!error && data?.current && !parts.search && !fileMode && (
        <div className="flex justify-end">
          <Button type="button" variant="primary" onClick={() => onSelect?.(data.current)}>
            选择当前目录
          </Button>
        </div>
      )}
      {!error && data?.current && parts.search && !fileMode && selectDirectories && (
        <div className="flex justify-end">
          <Button type="button" variant="secondary" onClick={() => onSelect?.(data.current)}>
            使用检索目录
          </Button>
        </div>
      )}
      {!error && data?.parent && !parts.search && !selectDirectories && (
        <div className="flex justify-start">
          <Button type="button" size="sm" variant="secondary" onClick={() => onChange(appendSeparator(data.parent!))}>
            <ChevronUp size={14} /> 上一级
          </Button>
        </div>
      )}
    </div>
  );
}
