import React, { useState } from 'react';
import ReactMarkdown, { type ExtraProps } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import rehypeKatex from 'rehype-katex';
import { Copy, Check } from 'lucide-react';
import 'highlight.js/styles/github-dark.css';

type CodeProps = React.JSX.IntrinsicElements['code'] & ExtraProps;
type PreProps = React.JSX.IntrinsicElements['pre'] & ExtraProps;

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

function DiffLines({ codeText, ...rest }: { codeText: string; [key: string]: unknown }) {
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

function CodeBlock({ className, children, ...props }: CodeProps) {
  const match = /language-(\w+)/.exec(className || '');
  const language = match ? match[1] : null;

  // Inline code — no language-* class means inline
  if (!language) {
    return (
      <code className="bg-bg-tertiary rounded px-1 py-0.5 text-[0.9em] font-mono" {...props}>
        {children}
      </code>
    );
  }

  // Extract raw text for copy and diff rendering
  const codeText = extractCodeText(children).replace(/\n$/, '');

  return (
    <div className="rounded-lg border border-border-default bg-bg-tertiary overflow-hidden my-3">
      <div className="flex items-center justify-between px-3 py-1 bg-bg-secondary border-b border-border-default">
        <span className="text-[11px] text-text-tertiary font-mono uppercase tracking-wider">
          {language}
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
  return <>{children}</>;
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
