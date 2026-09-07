import { useCallback, useEffect, useRef } from 'react';
import Editor from '@monaco-editor/react';
import type { OnMount, BeforeMount } from '@monaco-editor/react';
import {
  setMonacoRef,
  useEditorStore,
  languageFromPath,
  type EditorLocation,
} from '@/stores/editorStore';

interface CodeEditorProps {
  path: string | null;
  content: string | null;
}

const beforeMount: BeforeMount = (monaco) => {
  monaco.editor.defineTheme('pan-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: 'comment', foreground: '6e7681', fontStyle: 'italic' },
      { token: 'keyword', foreground: 'ff7b72' },
      { token: 'string', foreground: 'a5d6ff' },
      { token: 'number', foreground: '79c0ff' },
      { token: 'type', foreground: 'ffa657' },
      { token: 'function', foreground: 'd2a8ff' },
    ],
    colors: {
      'editor.background': '#0d1117',
      'editor.foreground': '#e6edf3',
      'editorLineNumber.foreground': '#6e7681',
      'editorLineNumber.activeForeground': '#e6edf3',
      'editor.lineHighlightBackground': '#161b22',
      'editorCursor.foreground': '#58a6ff',
      'editor.selectionBackground': '#264f78',
      'editorWidget.background': '#161b22',
      'editorWidget.border': '#30363d',
      'editorGutter.background': '#0d1117',
      'editorBracketMatch.background': '#264f78',
      'editorBracketMatch.border': '#58a6ff',
    },
  });
};

export function CodeEditor({ path, content }: CodeEditorProps) {
  const requestSave = useEditorStore((s) => s.requestSave);
  const markDirty = useEditorStore((s) => s.markDirty);
  const activePath = useEditorStore((s) => s.activePath);
  const pendingLocation = useEditorStore((s) => s.pendingLocation);
  const consumePendingLocation = useEditorStore((s) => s.consumePendingLocation);
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null);

  const applyLocation = useCallback(
    (editor: Parameters<OnMount>[0], location: EditorLocation | null) => {
      if (!location || location.path !== path) return;
      const model = editor.getModel();
      const lineCount = model?.getLineCount() ?? location.line;
      const line = Math.min(Math.max(1, location.line), lineCount);
      const endLine = Math.min(
        Math.max(line, location.endLine ?? line),
        lineCount,
      );
      const endColumn = model?.getLineMaxColumn(endLine) ?? 1;

      editor.setPosition({ lineNumber: line, column: 1 });
      editor.revealLineInCenter(line);
      editor.setSelection({
        startLineNumber: line,
        startColumn: 1,
        endLineNumber: endLine,
        endColumn,
      });
      consumePendingLocation(location);
    },
    [consumePendingLocation, path],
  );

  // The link may finish loading before Monaco mounts, or Monaco may mount
  // before the async store update reaches React. Applying in both places
  // makes location requests independent of that initialization timing.
  useEffect(() => {
    if (editorRef.current) applyLocation(editorRef.current, pendingLocation);
  }, [applyLocation, pendingLocation]);

  const handleMount: OnMount = useCallback(
    (editor, monaco) => {
      setMonacoRef(monaco);
      editorRef.current = editor;
      applyLocation(editor, useEditorStore.getState().pendingLocation);

      // Register Ctrl+S
      editor.addCommand(
        monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS,
        () => {
          requestSave();
        },
      );
    },
    [applyLocation, requestSave],
  );

  const handleChange = useCallback(
    (value: string | undefined) => {
      if (value !== undefined && activePath) {
        markDirty(activePath, value);
      }
    },
    [activePath, markDirty],
  );

  if (!path) {
    return (
      <div className="flex-1 flex items-center justify-center text-text-tertiary text-sm">
        Open a file to start editing
      </div>
    );
  }

  const language = languageFromPath(path);
  const displayContent = content ?? '';

  return (
    <div className="flex-1 overflow-hidden">
      <Editor
        key={path}
        path={path}
        defaultLanguage={language}
        defaultValue={displayContent}
        theme="pan-dark"
        beforeMount={beforeMount}
        onMount={handleMount}
        onChange={handleChange}
        options={{
          minimap: { enabled: false },
          fontSize: 13,
          fontFamily: "'Cascadia Code', 'Fira Code', Consolas, monospace",
          scrollBeyondLastLine: false,
          tabSize: 2,
          automaticLayout: true,
          lineNumbers: 'on',
          renderWhitespace: 'selection',
          wordWrap: 'off',
          padding: { top: 8, bottom: 8 },
        }}
        loading={
          <div className="flex-1 flex items-center justify-center text-text-tertiary text-sm">
            Loading editor...
          </div>
        }
      />
    </div>
  );
}
