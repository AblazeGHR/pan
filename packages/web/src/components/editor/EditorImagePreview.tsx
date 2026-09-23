import { useEffect, useState } from 'react';
import { Minus, Plus, Scan } from 'lucide-react';

interface EditorImagePreviewProps {
  src: string;
  alt: string;
}

export function EditorImagePreview({ src, alt }: EditorImagePreviewProps) {
  const [scale, setScale] = useState(1);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setScale(1);
    setFailed(false);
  }, [src]);

  const changeScale = (amount: number) => {
    setScale((current) => Math.min(4, Math.max(0.25, Math.round((current + amount) * 100) / 100)));
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-bg-primary" aria-label="图片预览">
      <div className="flex items-center justify-end gap-1 border-b border-border-default bg-bg-primary px-2 py-1">
        <button
          type="button"
          aria-label="缩小图片"
          title="缩小图片"
          onClick={() => changeScale(-0.25)}
          disabled={scale <= 0.25}
          className="flex h-7 w-7 items-center justify-center rounded text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary disabled:opacity-40"
        ><Minus size={14} /></button>
        <span className="min-w-12 text-center text-[11px] text-text-secondary" aria-live="polite">{Math.round(scale * 100)}%</span>
        <button
          type="button"
          aria-label="放大图片"
          title="放大图片"
          onClick={() => changeScale(0.25)}
          disabled={scale >= 4}
          className="flex h-7 w-7 items-center justify-center rounded text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary disabled:opacity-40"
        ><Plus size={14} /></button>
        <button
          type="button"
          aria-label="适合窗口"
          title="适合窗口"
          onClick={() => setScale(1)}
          className="flex h-7 w-7 items-center justify-center rounded text-text-tertiary hover:bg-bg-tertiary hover:text-text-primary"
        ><Scan size={14} /></button>
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-3 sm:p-6">
        {failed ? (
          <p role="alert" className="text-sm text-text-secondary">图片加载失败，请检查文件是否可用。</p>
        ) : (
          <button
            type="button"
            aria-label={`${alt}，点击切换适合窗口或 100% 缩放`}
            onClick={() => setScale((current) => current === 1 ? 2 : 1)}
            className="flex min-h-0 max-w-full cursor-zoom-in items-center justify-center border-0 bg-transparent p-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          >
            <img
              src={src}
              alt={alt}
              onError={() => setFailed(true)}
              className="max-h-full max-w-full object-contain"
              style={{
              maxHeight: 'calc(100vh - 12rem)',
              maxWidth: '100%',
                zoom: scale,
              }}
            />
          </button>
        )}
      </div>
    </section>
  );
}
