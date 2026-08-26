import { useEffect, useRef, useState } from 'react';

interface ModelSelectProps {
  /** 当前选中的模型 ID（可能不在 options 中——会话可能持有历史模型）。 */
  value: string;
  /** 可用模型列表（来自 adapter config.models）。 */
  options: string[];
  /** 选中某个模型时回调（选中即生效，由调用方决定交互语义）。 */
  onChange: (value: string) => void;
  className?: string;
  /** 覆盖按钮样式。默认是 settings 里的 combobox 样式；聊天输入区的 ModelPill
   *  传 PILL_CLASS 以保持 pill 外观。 */
  buttonClassName?: string;
  /** 覆盖下拉菜单定位/尺寸。默认向下展开（`top-full mt-1 w-full`）；
   *  ModelPill 传向上展开（`bottom-full mb-1`）以保持原交互。 */
  menuClassName?: string;
}

/**
 * 带关键字过滤的模型选择器（combobox）。
 *
 * 原生 `<select>` 在 opencode 这种几十上百个模型时不可搜索，此组件把下拉改为
 * 「显示当前值按钮 + 过滤输入框 + 过滤后的选项列表」：
 * - 点击按钮展开，输入框自动聚焦；
 * - 输入关键字按大小写不敏感子串过滤选项；
 * - 点击选项即选中并关闭（选中即生效，与原先 select 一致）；
 * - 当前值不在 options 中时仍显示在列表顶部（保留历史模型可重选）。
 *
 * 样式沿用项目 tailwind design token（bg-bg-* / border-border-* / text-text-*）。
 */
export function ModelSelect({
  value,
  options,
  onChange,
  className = '',
  buttonClassName,
  menuClassName,
}: ModelSelectProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const buttonClass =
    buttonClassName ??
    'flex w-full items-center justify-between gap-1 truncate rounded border border-border-default bg-bg-tertiary px-2 py-1 text-left text-xs text-text-primary hover:bg-bg-hover';
  const menuClass =
    menuClassName ?? 'absolute left-0 top-full z-40 mt-1 w-full';

  // 当前值不在 options 中时插入顶部，保证历史模型始终可选中/可显示。
  const allOptions = options.includes(value) ? options : [value, ...options];
  const q = query.trim().toLowerCase();
  const filtered = q ? allOptions.filter((m) => m.toLowerCase().includes(q)) : allOptions;

  // 展开时自动聚焦过滤输入框。
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // 点击外部 / Escape 关闭。
  useEffect(() => {
    if (!open) return;
    const onMouseDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  const select = (m: string) => {
    onChange(m);
    setOpen(false);
    setQuery('');
  };

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={value}
        className={buttonClass}
      >
        <span className="truncate">{value || '…'}</span>
        <span className="shrink-0 text-text-tertiary" aria-hidden>
          ▾
        </span>
      </button>

      {open && (
        <div
          className={`${menuClass} rounded border border-border-default bg-bg-secondary shadow-xl`}
        >
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="筛选模型…"
            role="combobox"
            aria-expanded
            aria-controls="model-select-list"
            className="w-full border-b border-border-muted bg-bg-tertiary px-2 py-1.5 text-xs text-text-primary outline-none placeholder:text-text-tertiary"
          />
          <ul
            id="model-select-list"
            role="listbox"
            className="max-h-56 overflow-y-auto"
          >
            {filtered.length === 0 && (
              <li className="px-2 py-1.5 text-xs text-text-tertiary">
                无匹配模型
              </li>
            )}
            {filtered.map((m) => (
              <li key={m}>
                <button
                  type="button"
                  role="option"
                  aria-selected={m === value}
                  onClick={() => select(m)}
                  title={m}
                  className={`block w-full truncate px-2 py-1 text-left text-xs hover:bg-bg-tertiary ${
                    m === value ? 'bg-accent/10 text-accent' : 'text-text-primary'
                  }`}
                >
                  {m}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
