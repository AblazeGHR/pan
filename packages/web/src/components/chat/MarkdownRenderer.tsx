import React, { createContext, useContext, useState } from 'react';
import ReactMarkdown, { type ExtraProps } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import { Copy, Check } from 'lucide-react';
import 'highlight.js/styles/github-dark.css';

type CodeProps = React.JSX.IntrinsicElements['code'] & ExtraProps;
type PreProps = React.JSX.IntrinsicElements['pre'] & ExtraProps;

/** True while rendering a <pre> subtree, i.e. a block-level code block.
 *  Inline code (backticks) is never wrapped in a <pre>. */
const PreContext = createContext(false);

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

/** Recursively extract plain text from React nodes (handles hljs spans). */
function extractCodeText(node: React.ReactNode): string {
  if (typeof node === 'string') return node;
  if (typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(extractCodeText).join('');
  if (React.isValidElement(node)) {
    return extractCodeText((node.props as { children?: React.ReactNode }).children);
  }
  return '';
}

function CopyButton({ codeText }: { codeText: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(codeText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      onClick={handleCopy}
      className="text-[10px] text-text-tertiary hover:text-text-primary cursor-pointer transition-colors"
    >
      {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
    </button>
  );
}

function DiffLines({ codeText, node: _node, ...rest }: { codeText: string; node?: unknown; [key: string]: unknown }) {
  return (
    <pre className="p-3 overflow-x-auto m-0">
      <code className="text-xs font-mono leading-relaxed block" {...rest}>
        {codeText.split('\n').map((line: string, i: number) => {
          let lineClass = '';
          if (line.startsWith('+') && !line.startsWith('+++')) {
            lineClass = 'bg-green-500/10 border-l-2 border-green-500 pl-2 -ml-2';
          } else if (line.startsWith('-') && !line.startsWith('---')) {
            lineClass = 'bg-red-500/10 border-l-2 border-red-500 pl-2 -ml-2';
          }
          return (
            <div key={i} className={lineClass} style={{ minHeight: '1.25em' }}>
              {line || '\u00A0'}
            </div>
          );
        })}
      </code>
    </pre>
  );
}

function CodeBlock({
  className,
  children,
  node: _node,
  ...props
}: CodeProps) {
  const isInPre = useContext(PreContext);
  // Support hyphenated language names (e.g. "shell-session")
  const match = /language-([\w-]+)/.exec(className || '');
  const language = match ? match[1] : null;

  // Inline code — only code NOT inside a <pre> is inline
  if (!isInPre) {
    return (
      <code className="bg-bg-tertiary rounded px-1 py-0.5 text-[0.9em] font-mono" {...props}>
        {children}
      </code>
    );
  }

  // Block code (fenced or indented) — with or without a language label
  const codeText = extractCodeText(children).replace(/\n$/, '');
  const langLabel = language || 'code';

  return (
    <div className="rounded-lg border border-border-default bg-bg-tertiary overflow-hidden my-3">
      <div className="flex items-center justify-between px-3 py-1 bg-bg-secondary border-b border-border-default">
        <span className="text-[11px] text-text-tertiary font-mono uppercase tracking-wider">
          {langLabel}
        </span>
        <CopyButton codeText={codeText} />
      </div>
      {language === 'diff' ? (
        <DiffLines codeText={codeText} {...props} />
      ) : (
        <pre className="p-3 overflow-x-auto m-0">
          <code className={`text-xs font-mono leading-relaxed ${className || ''}`} {...props}>
            {children as React.ReactNode}
          </code>
        </pre>
      )}
    </div>
  );
}

function PreBlock({ children }: PreProps) {
  return <PreContext.Provider value={true}>{children}</PreContext.Provider>;
}

export function MarkdownRenderer({ content, className = '' }: MarkdownRendererProps) {
  if (!content) return null;

  return (
    <div className={`prose-kimi max-w-none break-words ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight, rehypeKatex]}
        components={{
          code: CodeBlock,
          pre: PreBlock,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
