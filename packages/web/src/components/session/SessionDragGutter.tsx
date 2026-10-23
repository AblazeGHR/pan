import type { ComponentProps } from 'react';

/** Shared presentation; callers retain their own drag and persistence semantics. */
export function SessionDragGutter({ children, className = '', ...props }: ComponentProps<'span'>) {
  return (
    <span
      {...props}
      className={`drag-gutter relative z-[5] shrink-0 flex items-center justify-center w-5 self-stretch -my-2 -ml-1 -mr-1 cursor-grab active:cursor-grabbing select-none touch-none hover:bg-bg-hover/60 rounded-l-sm ${className}`}
    >
      <span
        aria-hidden="true"
        className="drag-matrix pointer-events-none absolute inset-x-0 top-[10px] bottom-[10px] rounded-sm"
      />
      <span className="relative z-[1] flex items-center">{children}</span>
    </span>
  );
}
