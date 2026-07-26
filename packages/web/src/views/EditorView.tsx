export default function EditorView() {
  return (
    <div className="flex h-full">
      <div className="w-56 border-r border-border-default bg-bg-secondary p-2 text-sm text-text-secondary">
        文件树 (开发中)
      </div>
      <div className="flex-1 flex items-center justify-center text-text-tertiary">
        Monaco 编辑器 (开发中)
      </div>
    </div>
  );
}
